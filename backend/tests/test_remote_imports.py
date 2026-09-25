from __future__ import annotations

import copy
import hashlib
import shutil
import threading
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from fastapi import Request

from spectarr import pride, remote_worker
from spectarr.config import get_settings
from spectarr.database import SessionLocal
from spectarr.models import Artifact, ProjectMembership, RemoteImport, Run, User, UserRole, utcnow

PAYLOAD = b'BEGIN IONS\nTITLE=remote-test\nPEPMASS=500\n100 50\nEND IONS\n'
URL = 'https://ftp.pride.ebi.ac.uk/pride/data/archive/2012/03/PXD000001/test.mgf'
DATASET = {
    'accession': 'PXD000001', 'title': 'Public test study', 'description': 'Test description',
    'url': 'https://www.ebi.ac.uk/pride/archive/projects/PXD000001', 'doi': '10.6019/PXD000001',
    'files': [{'id': 'file-1', 'filename': 'test.mgf', 'byte_size': len(PAYLOAD), 'category': 'PEAK',
               'url': URL, 'supported': True, 'checksum': {'algorithm': 'md5', 'value': hashlib.md5(PAYLOAD).hexdigest()}}],
}


@pytest.fixture
def repository(monkeypatch):
    monkeypatch.setattr(pride, 'lookup', lambda value: copy.deepcopy(DATASET))
    monkeypatch.setattr(pride, 'check_public_host', lambda host: None)
    requests = []
    def handle(request):
        requests.append(request)
        start = int(request.headers.get('range', 'bytes=0-')[6:-1])
        headers = {'etag': '"v1"', 'content-length': str(len(PAYLOAD) - start)}
        if start:
            headers['content-range'] = f'bytes {start}-{len(PAYLOAD) - 1}/{len(PAYLOAD)}'
        return httpx.Response(206 if start else 200, stream=httpx.ByteStream(PAYLOAD[start:]), headers=headers)
    real_client = httpx.Client
    monkeypatch.setattr(pride, 'client', lambda: real_client(transport=httpx.MockTransport(handle)))
    return requests


async def enqueue(client, hierarchy, **changes):
    payload = {'accession': 'PXD000001', 'experiment_id': hierarchy['experiment_id'],
               'files': [{'file_id': 'file-1', 'run_name': 'remote run', 'sample_name': 'remote sample'}], **changes}
    return await client.post('/api/v1/remote-imports', json=payload, headers={'Idempotency-Key': str(uuid4())})


def row(import_id):
    with SessionLocal() as session:
        return session.get(RemoteImport, import_id)


def partial(item, content=PAYLOAD[:10], validator='"v1"'):
    storage = remote_worker.storage_for_settings()
    path = storage.staging / 'remote-imports' / f'{item.id}.part'
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    with SessionLocal() as session:
        current = session.get(RemoteImport, item.id)
        current.state = 'downloading'
        current.validator = validator
        current.bytes_received = len(content)
        session.commit()
    return path


@pytest.mark.anyio
async def test_lookup_enqueue_download_registration_and_idempotency(client, hierarchy, repository):
    lookup = await client.get('/api/v1/repositories/pride', params={'accession': 'PXD000001', 'project_id': hierarchy['project_id']})
    assert lookup.status_code == 200
    assert 'url' not in lookup.json()['files'][0]
    assert lookup.json()['free_bytes'] > 0
    key = {'Idempotency-Key': str(uuid4())}
    data = {'accession': 'PXD000001', 'experiment_id': hierarchy['experiment_id'],
            'files': [{'file_id': 'file-1', 'run_name': 'download', 'sample_name': 'sample'}]}
    first = await client.post('/api/v1/remote-imports', json=data, headers=key)
    assert first.status_code == 202, first.text
    second = await client.post('/api/v1/remote-imports', json=data, headers=key)
    assert first.json() == second.json()
    changed = {**data, 'files': [{**data['files'][0], 'run_name': 'changed'}]}
    assert (await client.post('/api/v1/remote-imports', json=changed, headers=key)).status_code == 409
    item = first.json()[0]
    remote_worker.tick()
    complete = row(item['id'])
    assert complete.state == 'succeeded', complete.error
    with SessionLocal() as session:
        artifact = session.get(Artifact, complete.artifact_id)
        assert artifact.sha256 == hashlib.sha256(PAYLOAD).hexdigest()
        assert artifact.metadata_json['dataset']['doi'] == DATASET['doi']
        assert len(artifact.run.sample_links) == 1
    downloaded = await client.get(f'/api/v1/artifacts/{complete.artifact_id}/download')
    assert downloaded.content == PAYLOAD
    listing = (await client.get('/api/v1/remote-imports')).json()
    assert listing[0]['state'] == 'succeeded'
    lookup = await client.get('/api/v1/repositories/pride', params={'accession': 'PXD000001', 'project_id': hierarchy['project_id']})
    assert lookup.json()['files'][0]['previously_imported']
    assert len(repository) == 1


@pytest.mark.anyio
async def test_resume_after_process_restart_and_registration_crash(client, hierarchy, repository):
    response = await enqueue(client, hierarchy)
    item = row(response.json()[0]['id'])
    path = partial(item)
    remote_worker.tick()
    assert repository[0].headers['range'] == 'bytes=10-'
    assert repository[0].headers['if-range'] == '"v1"'
    complete = row(item.id)
    assert complete.state == 'succeeded', complete.error
    assert not path.exists()
    with SessionLocal() as session:
        current = session.get(RemoteImport, item.id)
        current.state = 'registering'
        current.artifact_id = None
        current.run_id = None
        session.commit()
    remote_worker.tick()
    assert row(item.id).artifact_id == complete.artifact_id
    assert len(repository) == 1
    with SessionLocal() as session:
        assert session.scalar(select(func.count(Artifact.id))) == 1
        assert session.scalar(select(func.count(Run.id)).where(Run.name == 'remote run')) == 1


@pytest.mark.anyio
@pytest.mark.parametrize('response_kind', ['ignored', 'changed', 'bad_range', 'oversized', 'checksum', 'short', 'redirect'])
async def test_transfer_failures_never_register_corrupt_sources(client, hierarchy, repository, monkeypatch, response_kind):
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    path = partial(item)
    def handle(request):
        if response_kind == 'ignored':
            return httpx.Response(200, stream=httpx.ByteStream(PAYLOAD), headers={'etag': '"new"'})
        if response_kind == 'changed':
            return httpx.Response(206, stream=httpx.ByteStream(PAYLOAD[10:]), headers={'etag': '"new"', 'content-range': f'bytes 10-{len(PAYLOAD)-1}/{len(PAYLOAD)}'})
        if response_kind == 'bad_range':
            return httpx.Response(206, stream=httpx.ByteStream(PAYLOAD), headers={'etag': '"v1"', 'content-range': f'bytes 0-{len(PAYLOAD)-1}/{len(PAYLOAD)}'})
        if response_kind == 'oversized':
            return httpx.Response(200, stream=httpx.ByteStream(PAYLOAD + b'x'))
        if response_kind == 'checksum':
            return httpx.Response(200, stream=httpx.ByteStream(b'x' * len(PAYLOAD)))
        if response_kind == 'short':
            return httpx.Response(200, stream=httpx.ByteStream(PAYLOAD[:5]))
        return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    remote_worker.tick()
    current = row(item.id)
    if response_kind == 'ignored':
        assert current.state == 'succeeded', current.error
    else:
        assert current.state == ('queued' if response_kind == 'short' else 'failed'), current.error
        assert current.artifact_id is None
        with SessionLocal() as session:
            assert session.scalar(select(func.count(Artifact.id))) == 0
    if response_kind in {'changed', 'bad_range', 'checksum'}:
        assert not path.exists()


@pytest.mark.anyio
async def test_cancel_retry_stop_and_capacity(client, hierarchy, repository, monkeypatch):
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    partial(item)
    cancelled = await client.post(f'/api/v1/remote-imports/{item.id}/cancel')
    assert cancelled.json()['cancel_requested']
    remote_worker.tick()
    assert row(item.id).state == 'cancelled'
    assert repository == []
    assert (await client.post(f'/api/v1/remote-imports/{item.id}/retry')).status_code == 200
    stop = threading.Event()
    stop.set()
    remote_worker.tick(stop)
    assert row(item.id).state == 'downloading'
    original = shutil.disk_usage
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: original(path)._replace(free=0))
    remote_worker.tick()
    assert row(item.id).state == 'failed'
    assert 'disk space' in row(item.id).error
    assert (await enqueue(client, hierarchy)).status_code == 409
    monkeypatch.setattr(shutil, 'disk_usage', original)
    await client.post(f'/api/v1/remote-imports/{item.id}/retry')
    remote_worker.tick()
    assert row(item.id).state == 'succeeded'
    assert (await client.post(f'/api/v1/remote-imports/{item.id}/retry')).status_code == 409


@pytest.mark.anyio
async def test_transient_retries_are_bounded_and_restore_is_inert(client, hierarchy, repository, monkeypatch):
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))))
    for attempt in range(3):
        remote_worker.tick()
        current = row(item.id)
        assert current.attempts == attempt + 1
        assert current.state == ('failed' if attempt == 2 else 'queued')
        with SessionLocal() as session:
            session.get(RemoteImport, item.id).retry_at = utcnow() - timedelta(seconds=1)
            session.commit()
    await client.post(f'/api/v1/remote-imports/{item.id}/retry')
    monkeypatch.setattr(get_settings(), 'restore_mode', True)
    remote_worker.tick()
    assert row(item.id).attempts == 0


@pytest.mark.anyio
async def test_input_validation_and_provider_errors(client, hierarchy, repository, monkeypatch):
    assert (await enqueue(client, hierarchy, accession='http://127.0.0.1/')).status_code == 422
    assert (await enqueue(client, hierarchy, files=[])).status_code == 422
    assert (await enqueue(client, hierarchy, files=[{'file_id': 'missing', 'run_name': 'x', 'sample_name': 'x'}])).status_code == 422
    assert (await enqueue(client, hierarchy, files=[{'file_id': 'file-1', 'run_name': ' ', 'sample_name': 'x'}])).status_code == 422
    choice = {'file_id': 'file-1', 'run_name': 'x', 'sample_name': 'x'}
    assert (await enqueue(client, hierarchy, files=[choice, choice])).status_code == 422
    def fail(value):
        raise httpx.ConnectError('unavailable')
    monkeypatch.setattr(pride, 'lookup', fail)
    assert (await client.get('/api/v1/repositories/pride?accession=PXD000001')).status_code == 502


@pytest.mark.anyio
async def test_project_authorization_before_artifact_exists(client, hierarchy, repository, monkeypatch):
    from spectarr.auth import Principal, require_request_access
    from spectarr.main import app
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    with SessionLocal() as session:
        user = User(username='restricted', password_hash='!', role=UserRole.VIEWER)
        session.add(user)
        session.commit()
        user_id = user.id
    principal = Principal('user', user_id, UserRole.VIEWER, frozenset({'library:read', 'library:write'}), user_id=user_id)
    def access(request: Request):
        request.state.principal = principal
        return principal
    app.dependency_overrides[require_request_access] = access
    try:
        assert (await client.get('/api/v1/settings/downloads')).status_code == 403
        assert (await client.put('/api/v1/settings/downloads', json={'concurrency': 8})).status_code == 403
        assert (await client.get('/api/v1/remote-imports')).json() == []
        assert (await client.post(f'/api/v1/remote-imports/{item.id}/cancel')).status_code == 403
        assert (await enqueue(client, hierarchy)).status_code == 403
        assert (await client.get('/api/v1/repositories/pride', params={'accession': 'PXD000001', 'project_id': hierarchy['project_id']})).status_code == 403
        with SessionLocal() as session:
            session.add(ProjectMembership(project_id=hierarchy['project_id'], user_id=user_id, role=UserRole.VIEWER))
            session.commit()
        assert len((await client.get('/api/v1/remote-imports')).json()) == 1
        assert (await client.post(f'/api/v1/remote-imports/{item.id}/cancel')).status_code == 403
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize('value', ['https://evil.test/file.raw', 'ftp://ftp.pride.ebi.ac.uk/pride/data/archive/../x',
                                   'https://ftp.pride.ebi.ac.uk@127.0.0.1/x', 'https://ftp.pride.ebi.ac.uk:8080/pride/data/archive/x',
                                   'https://ftp.pride.ebi.ac.uk/pride/data/archive/%2e%2e/x',
                                   'https://ftp.pride.ebi.ac.uk/pride/data/archive/x?token=secret'])
def test_provider_url_policy(value):
    with pytest.raises(ValueError):
        pride.download_url(value)


def test_live_response_shapes_without_network(monkeypatch):
    records = [{'accession': 'file', 'fileName': 'test.mgf', 'fileSizeBytes': 10,
                'checksum': '', 'fileCategory': {'value': 'PEAK'}, 'publicFileLocations': [{'value': URL.replace('https:', 'ftp:')}]}]
    def handle(request):
        return httpx.Response(200, json=records if request.url.path.endswith('/files/all') else {k: v for k, v in DATASET.items() if k != 'files'})
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    monkeypatch.setattr(pride, 'check_public_host', lambda host: None)
    dataset = pride.lookup('https://www.ebi.ac.uk/pride/archive/projects/PXD000001')
    assert dataset['files'][0]['supported']
    assert dataset['files'][0]['checksum'] is None
    assert pride.checksum('sha-256:' + 'a' * 64) == {'algorithm': 'sha256', 'value': 'a' * 64}
    assert pride.checksum('md5:' + 'a' * 64) is None
    records[0]['fileName'] = 'vendor.d.zip'
    assert not pride.lookup('pxd000001')['files'][0]['supported']


@pytest.mark.anyio
async def test_http_size_corrects_stale_listing_and_preserves_provenance(client, hierarchy, repository, monkeypatch):
    dataset = copy.deepcopy(DATASET)
    dataset['files'][0]['byte_size'] = len(PAYLOAD) * 3
    monkeypatch.setattr(pride, 'lookup', lambda value: copy.deepcopy(dataset))
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    remote_worker.tick()
    result = row(item.id)
    assert result.state == 'succeeded', result.error
    assert result.byte_size == result.bytes_received == len(PAYLOAD)
    with SessionLocal() as session:
        source = session.get(Artifact, result.artifact_id)
        assert source.metadata_json['repository_reported_bytes'] == len(PAYLOAD) * 3
        assert source.metadata_json['downloaded_bytes'] == len(PAYLOAD)
        assert source.metadata_json['retrieved_at']


@pytest.mark.anyio
async def test_changed_http_size_still_enforces_limit(client, hierarchy, repository, monkeypatch):
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, headers={'content-length': str(get_settings().max_upload_bytes + 1)}, stream=httpx.ByteStream(b''))
    )))
    remote_worker.tick()
    assert row(item.id).state == 'failed'
    assert 'oversized' in row(item.id).error


@pytest.mark.anyio
async def test_parallel_slots_overlap_without_duplicate_transfers(client, hierarchy, repository, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    dataset = copy.deepcopy(DATASET)
    dataset['files'].append({**dataset['files'][0], 'id': 'file-2', 'filename': 'second.mgf', 'url': URL.replace('test.mgf', 'second.mgf')})
    monkeypatch.setattr(pride, 'lookup', lambda value: copy.deepcopy(dataset))
    selections = [{'file_id': file['id'], 'run_name': file['filename'], 'sample_name': 'shared'} for file in dataset['files']]
    response = await enqueue(client, hierarchy, files=selections)
    assert response.status_code == 202
    barrier = threading.Barrier(2)
    seen = []
    def handle(request):
        seen.append(str(request.url))
        barrier.wait(timeout=8)
        return httpx.Response(200, stream=httpx.ByteStream(PAYLOAD), headers={'etag': '"v1"', 'content-length': str(len(PAYLOAD))})
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(remote_worker.tick, None, slot) for slot in [0, 1, 0]]
        for future in futures:
            future.result(timeout=15)
    assert len(seen) == len(set(seen)) == 2
    assert all(row(item['id']).state == 'succeeded' for item in response.json())
    assert all(row(item['id']).reserved_bytes == 0 for item in response.json())
    with SessionLocal() as session:
        from spectarr.models import Sample
        assert session.scalar(select(func.count(Sample.id)).where(Sample.name == 'shared')) == 1


@pytest.mark.anyio
async def test_capacity_reservations_account_for_other_active_files(client, hierarchy, repository, monkeypatch):
    from spectarr.remote_capacity import item_lock, reserve, release
    first = row((await enqueue(client, hierarchy)).json()[0]['id'])
    second = row((await enqueue(client, hierarchy)).json()[0]['id'])
    storage = remote_worker.storage_for_settings()
    original = shutil.disk_usage
    monkeypatch.setattr(shutil, 'disk_usage', lambda path: original(path)._replace(free=256 * 1024 * 1024 + 200))
    with item_lock(storage, first.id):
        reserve(storage, first.id, 57, 0, 3)
        with item_lock(storage, second.id), pytest.raises(ValueError, match='other active'):
            reserve(storage, second.id, 57, 0, 3)
    # The first owner died without releasing. Its unlocked reservation is reclaimed.
    with item_lock(storage, second.id):
        reserve(storage, second.id, 57, 0, 3)
    assert row(first.id).reserved_bytes == 0
    assert row(second.id).reserved_bytes == 171
    release(storage, second.id)
    assert row(second.id).reserved_bytes == 0


@pytest.fixture
def sdrf_repository(repository, monkeypatch):
    columns = ['source name', 'assay name', 'comment[data file]', 'comment[label]', 'characteristics[organism]', 'factor value[treatment]']
    rows = [
        ['control', 'plex', 'test.mgf', 'TMT126', 'Homo sapiens', 'control'],
        ['treated', 'plex', 'test.mgf', 'TMT127N', 'Homo sapiens', 'drug'],
        ['control', 'second', 'second.mgf', 'label free sample', 'Homo sapiens', 'control'],
        ['unselected', 'missing', 'missing.raw', 'label free sample', 'Homo sapiens', 'control'],
    ]
    from spectarr.sdrf import serialize_sdrf
    content = serialize_sdrf(columns, rows)
    dataset = copy.deepcopy(DATASET)
    dataset['files'].append({**dataset['files'][0], 'id': 'file-2', 'filename': 'second.mgf', 'url': URL.replace('test.mgf', 'second.mgf')})
    dataset['files'].append({'id': 'sdrf-1', 'filename': 'sdrf.tsv', 'byte_size': len(content), 'is_sdrf': True, 'supported': False,
                             'url': URL.replace('test.mgf', 'sdrf.tsv'), 'checksum': None})
    monkeypatch.setattr(pride, 'lookup', lambda value: copy.deepcopy(dataset))
    requests = []
    def handle(request):
        requests.append(str(request.url))
        data = content if request.url.path.endswith('sdrf.tsv') else PAYLOAD
        return httpx.Response(200, stream=httpx.ByteStream(data), headers={'etag': '"v1"', 'content-length': str(len(data))})
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    return dataset, content, requests


@pytest.mark.anyio
async def test_sdrf_parallel_import_appends_preserves_labels_and_recovers(client, hierarchy, sdrf_repository):
    from concurrent.futures import ThreadPoolExecutor
    from spectarr.models import SdrfDocument
    existing = (await client.post(f'/api/v1/projects/{hierarchy["project_id"]}/sdrf/generate')).json()
    preview = await client.get('/api/v1/repositories/pride/sdrf?accession=PXD000001&file_id=sdrf-1')
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan['mappings'] == {'file-1': [0, 1], 'file-2': [2]}
    assert len(plan['warnings']) == 1
    choices = [{'file_id': file_id, 'run_name': file_id, 'sample_name': 'unused placeholder'} for file_id in ['file-1', 'file-2']]
    created = await enqueue(client, hierarchy, files=choices, sdrf_file_id='sdrf-1', sdrf_sha256=plan['sha256'])
    assert created.status_code == 202, created.text
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(remote_worker.tick, None, slot) for slot in [0, 1]]
        for future in futures:
            future.result(timeout=15)
    imports = [row(item['id']) for item in created.json()]
    assert [item.state for item in imports] == ['succeeded', 'succeeded'], [item.error for item in imports]
    with SessionLocal() as session:
        document = session.scalar(select(SdrfDocument).where(SdrfDocument.project_id == hierarchy['project_id']))
        assert len(document.rows) == len(existing['rows']) + 3
        assert document.rows[0].values[:len(existing['columns'])] == existing['rows'][0]['values']
        first = session.get(Artifact, next(item.artifact_id for item in imports if item.file_id == 'file-1'))
        assert [(link.sample.name, link.label) for link in first.run.sample_links] == [('control', 'TMT126'), ('treated', 'TMT127N')]
        assert first.metadata_json['sdrf_import']['source_rows'] == [2, 3]
        assert first.run.sample_id is None
        assert first.run.sample_links[1].metadata_json['factor value[treatment]'] == 'drug'
        current = session.get(RemoteImport, imports[0].id)
        current.state = 'registering'
        current.artifact_id = None
        session.commit()
    requests_before = len(sdrf_repository[2])
    remote_worker.tick()
    assert row(imports[0].id).state == 'succeeded'
    assert len(sdrf_repository[2]) == requests_before
    document = (await client.get(f'/api/v1/projects/{hierarchy["project_id"]}/sdrf')).json()
    assert len(document['rows']) == len(existing['rows']) + 3


@pytest.mark.anyio
async def test_sdrf_preview_hash_and_subset_enforced(client, hierarchy, sdrf_repository):
    plan = (await client.get('/api/v1/repositories/pride/sdrf?accession=PXD000001&file_id=sdrf-1')).json()
    assert (await enqueue(client, hierarchy, sdrf_file_id='sdrf-1')).status_code == 422
    assert (await enqueue(client, hierarchy, sdrf_file_id='sdrf-1', sdrf_sha256='0' * 64)).status_code == 409
    imported = await enqueue(client, hierarchy, sdrf_file_id='sdrf-1', sdrf_sha256=plan['sha256'])
    remote_worker.tick()
    result = row(imported.json()[0]['id'])
    assert result.state == 'succeeded', result.error
    doc = (await client.get(f'/api/v1/projects/{hierarchy["project_id"]}/sdrf')).json()
    assert len(doc['rows']) == 2
    assert all(entry['run_id'] == result.run_id for entry in doc['rows'])
    assert (await client.get('/api/v1/repositories/pride/sdrf?accession=PXD000001&file_id=file-1')).status_code == 422


@pytest.mark.anyio
async def test_sdrf_network_failure_does_not_prevent_plain_import(client, hierarchy, sdrf_repository, monkeypatch):
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))))
    response = await client.get('/api/v1/repositories/pride/sdrf?accession=PXD000001&file_id=sdrf-1')
    assert response.status_code == 502
    assert (await enqueue(client, hierarchy)).status_code == 202


@pytest.mark.anyio
async def test_sdrf_rejects_malformed_and_ambiguous_mapping(client, hierarchy, sdrf_repository, monkeypatch):
    from spectarr import remote_sdrf
    dataset, _, _ = sdrf_repository
    contents = [b'source name\nexample\n', b'source name\tcomment[data file]\nx\n']
    for content in contents:
        monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=content))))
        assert (await client.get('/api/v1/repositories/pride/sdrf?accession=PXD000001&file_id=sdrf-1')).status_code == 422
    content = b'source name\tcomment[data file]\nexample\ttest.mgf\n'
    dataset['files'].append({**dataset['files'][0], 'id': 'duplicate'})
    result = remote_sdrf.preview(dataset, 'sdrf-1')
    assert result['mappings'] == {}
    assert result['warnings']


@pytest.mark.anyio
async def test_killed_worker_resumes_from_durable_partial_file(client, hierarchy, repository, monkeypatch, tmp_path):
    import os
    import subprocess
    import sys
    import time
    data = b'x' * (2 * 1024 * 1024)
    dataset = copy.deepcopy(DATASET)
    dataset['files'][0].update(byte_size=len(data), checksum={'algorithm': 'sha256', 'value': hashlib.sha256(data).hexdigest()})
    monkeypatch.setattr(pride, 'lookup', lambda value: copy.deepcopy(dataset))
    result = await enqueue(client, hierarchy)
    item = row(result.json()[0]['id'])
    marker = tmp_path / 'first-chunk'
    range_file = tmp_path / 'range'
    script = tmp_path / 'worker.py'
    script.write_text('''import sys, time
from pathlib import Path
import httpx
from spectarr import pride, remote_worker
pride.check_public_host = lambda host: None
marker, range_file, mode = map(str, sys.argv[1:])
class Content(httpx.SyncByteStream):
    def __init__(self, offset):
        self.offset = offset
    def __iter__(self):
        for position in range(self.offset, 2 * 1024 * 1024, 1024 * 1024):
            yield b'x' * (1024 * 1024)
            if mode == 'interrupt':
                Path(marker).write_text('ready')
                time.sleep(60)
def handle(request):
    value = request.headers.get('range', '')
    Path(range_file).write_text(value)
    offset = int(value[6:-1]) if value else 0
    headers = {'etag': '"version-1"', 'content-length': str(2 * 1024 * 1024 - offset)}
    if offset:
        headers['content-range'] = f'bytes {offset}-{2 * 1024 * 1024 - 1}/{2 * 1024 * 1024}'
    return httpx.Response(206 if offset else 200, stream=Content(offset), headers=headers)
pride.client = lambda: httpx.Client(transport=httpx.MockTransport(handle))
remote_worker.tick()
''')
    environment = {**os.environ, 'PYTHONPATH': str(__import__('pathlib').Path(remote_worker.__file__).parents[1])}
    command = [sys.executable, str(script), str(marker), str(range_file)]
    child = subprocess.Popen([*command, 'interrupt'], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.02)
        assert marker.exists(), child.poll()
        child.kill()
        child.communicate(timeout=5)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
    assert row(item.id).state == 'downloading'
    resumed = subprocess.run([*command, 'resume'], env=environment, capture_output=True, text=True, timeout=12)
    assert resumed.returncode == 0, resumed.stderr
    assert range_file.read_text() == 'bytes=1048576-'
    complete = row(item.id)
    assert complete.state == 'succeeded', complete.error
    assert complete.reserved_bytes == 0


@pytest.mark.anyio
async def test_download_settings_persist_apply_to_slots_and_reset(client, hierarchy, repository):
    from spectarr.remote_settings import download_settings
    default = get_settings().remote_download_concurrency
    response = await client.get('/api/v1/settings/downloads')
    assert response.status_code == 200
    assert response.json()['concurrency'] == default
    for invalid in [0, 9, 1.5, True, '4']:
        assert (await client.put('/api/v1/settings/downloads', json={'concurrency': invalid})).status_code == 422
    assert (await client.put('/api/v1/settings/downloads', json={'concurrency': 1})).json()['overridden']
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    remote_worker.tick(slot=1)
    assert row(item.id).state == 'queued'
    await client.put('/api/v1/settings/downloads', json={'concurrency': 3})
    assert download_settings()['concurrency'] == 3
    assert (await client.get('/api/v1/repositories/pride?accession=PXD000001')).json()['download_concurrency'] == 3
    remote_worker.tick(slot=2)
    assert row(item.id).state == 'succeeded'
    reset = await client.put('/api/v1/settings/downloads', json={'concurrency': None})
    assert reset.json()['concurrency'] == default
    assert not download_settings()['overridden']


@pytest.mark.anyio
async def test_download_setting_decrease_does_not_interrupt_active_transfer(client, hierarchy, repository, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    entered, finish = threading.Event(), threading.Event()
    def handle(request):
        entered.set()
        assert finish.wait(5)
        return httpx.Response(200, stream=httpx.ByteStream(PAYLOAD))
    monkeypatch.setattr(pride, 'client', lambda: httpx.Client(transport=httpx.MockTransport(handle)))
    await client.put('/api/v1/settings/downloads', json={'concurrency': 2})
    item = row((await enqueue(client, hierarchy)).json()[0]['id'])
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(remote_worker.tick, None, 1)
        try:
            assert entered.wait(5)
            await client.put('/api/v1/settings/downloads', json={'concurrency': 1})
        finally:
            finish.set()
        future.result(timeout=5)
    assert row(item.id).state == 'succeeded'


@pytest.mark.anyio
async def test_download_settings_read_only_during_restore(client, monkeypatch):
    monkeypatch.setenv('SPECTARR_RESTORE_MODE', 'true')
    get_settings.cache_clear()
    try:
        status = (await client.get('/api/v1/settings/downloads')).json()
        assert status['restore_mode'] and not status['enabled']
        assert (await client.put('/api/v1/settings/downloads', json={'concurrency': 4})).status_code == 503
    finally:
        get_settings.cache_clear()
