from __future__ import annotations

import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from spectarr.config import get_settings
from spectarr.database import SessionLocal
from spectarr.maintenance import maintenance_lock, sweep_storage, upload_lock
from spectarr.models import Artifact, Experiment, ProcessingBatch, Project, Run, UploadSession, UploadState
from spectarr.storage import LocalArtifactStorage

from test_platform import bootstrap

pytestmark = pytest.mark.anyio


async def post(client, path, headers, **kwargs):
    response = await client.post('/api/v1' + path, headers=headers, **kwargs)
    assert response.status_code < 400, response.text
    return response.json()


async def secured_run(client, headers, name):
    project = await post(client, '/projects', headers, json={'name': name})
    experiment = await post(client, '/experiments', headers, json={'project_id': project['id'], 'name': name})
    run = await post(client, '/runs', headers, json={'experiment_id': experiment['id'], 'name': name})
    artifact = await post(client, f'/runs/{run["id"]}/artifacts/upload', headers, files={'file': (name + '.mgf', name.encode())})
    return project, experiment, run, artifact


async def test_visibility_applies_to_collections_aggregates_batches_and_read_queries(client, password_auth):
    _, admin = await bootstrap(client)
    visible = await secured_run(client, admin, 'Visible')
    hidden = await secured_run(client, admin, 'Hidden')
    user = await post(client, '/users', admin, json={'username': 'reader', 'password': 'reader-test-password', 'role': 'viewer'})
    await post(client, f'/projects/{visible[0]["id"]}/memberships', admin, json={'user_id': user['id'], 'role': 'viewer'})
    login = await post(client, '/auth/login', None, json={'username': 'reader', 'password': 'reader-test-password'})
    viewer = {'Authorization': 'Bearer ' + login['access_token']}
    with SessionLocal() as session:
        batches = [ProcessingBatch(scope_type='project', scope_ids=[record[0]['id']], mode='ensure', label=record[0]['name']) for record in (visible, hidden)]
        session.add_all(batches)
        session.commit()
        hidden_batch = batches[1].id
    for endpoint, expected in [('/projects', visible[0]['id']), ('/experiments', visible[1]['id']), ('/runs', visible[2]['id']), ('/artifacts', visible[3]['id'])]:
        response = await client.get('/api/v1' + endpoint, headers=viewer)
        assert response.status_code == 200
        assert [item['id'] for item in response.json()] == [expected]
    assert (await client.get('/api/v1/experiments', params={'project_id': hidden[0]['id']}, headers=viewer)).json() == []
    overview = (await client.get('/api/v1/overview', headers=viewer)).json()
    assert overview['stats']['runs'] == 1
    assert [run['id'] for run in overview['runs']] == [visible[2]['id']]
    jobs = (await client.get('/api/v1/jobs', headers=viewer)).json()
    assert all(job['input_artifact_id'] == visible[3]['id'] for job in jobs)
    events = (await client.get('/api/v1/events/outbox', headers=viewer)).json()
    assert events and all(event['payload']['project_id'] == visible[0]['id'] for event in events)
    assert len((await client.get('/api/v1/processing-batches', headers=viewer)).json()) == 1
    assert (await client.get('/api/v1/processing-batches/' + hidden_batch, headers=viewer)).status_code == 403
    for artifact, expected in [(visible[3], 409), (hidden[3], 403)]:
        response = await client.post(f'/api/v1/artifacts/{artifact["id"]}/spectra/query', headers=viewer, json={})
        assert response.status_code == expected
        if expected == 409:
            assert 'catalog' in response.json()['detail']
    # A read-scoped API key uses the same semantic read check as a session.
    token = await post(client, '/tokens', admin, json={'user_id': user['id'], 'name': 'reader token', 'scopes': ['library:read']})
    response = await client.post(f'/api/v1/artifacts/{visible[3]["id"]}/spectra/query', headers={'Authorization': 'Bearer ' + token['token']}, json={})
    assert response.status_code == 409


async def test_run_search_and_pagination_cover_older_projects_and_literal_search(client, hierarchy):
    with SessionLocal() as session:
        project = Project(name='Other project')
        experiment = Experiment(project=project, name='Other experiment')
        session.add(experiment)
        session.flush()
        session.add_all(Run(experiment=experiment, name=f'Newer {i}') for i in range(110))
        oldest = session.get(Run, hierarchy['run_id'])
        oldest.name = 'Old_100%'
        session.commit()
    first = (await client.get('/api/v1/runs?page=true&limit=50')).json()
    second = (await client.get('/api/v1/runs?page=true&limit=50&offset=50')).json()
    assert first['total'] == second['total'] == 111
    assert first['next_offset'] == 50
    assert not {r['id'] for r in first['items']} & {r['id'] for r in second['items']}
    for params in [{'project_id': hierarchy['project_id']}, {'query': 'HeLa'}, {'query': '_100%'}]:
        page = (await client.get('/api/v1/runs', params={'page': 'true', **params})).json()
        assert [run['id'] for run in page['items']] == [hierarchy['run_id']]
        assert page['total'] == 1
        assert page['next_offset'] is None
    assert (await client.get('/api/v1/runs?offset=-1')).status_code == 422


async def agent_upload(client, admin, run_id, content, key):
    agent = await post(client, '/agents/register', admin, json={'name': 'Agent ' + key})
    headers = {'Authorization': 'Bearer ' + agent['token'], 'Idempotency-Key': key}
    payload = {'run_id': run_id, 'filename': key + '.raw', 'format': 'RAW', 'total_size': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
    upload = await post(client, '/upload-sessions', headers, json=payload)
    return headers, payload, upload


async def test_upload_expiry_cleanup_and_interrupted_verification_are_recoverable(client, password_auth):
    _, admin = await bootstrap(client)
    _, _, run, _ = await secured_run(client, admin, 'Uploads')
    content = b'recoverable upload data'
    headers, payload, upload = await agent_upload(client, admin, run['id'], content, 'recover')
    path = '/api/v1/upload-sessions/' + upload['id']
    with SessionLocal() as session:
        record = session.get(UploadSession, upload['id'])
        record.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    assert (await client.get(path, headers=headers)).status_code == 410
    resumed = await post(client, '/upload-sessions', headers, json=payload)
    assert resumed['id'] == upload['id'] and resumed['state'] == 'open'
    response = await client.patch(path, headers={**headers, 'Upload-Offset': '0'}, content=content)
    assert response.status_code == 204
    with SessionLocal() as session:
        record = session.get(UploadSession, upload['id'])
        record.state = UploadState.VERIFYING
        staging = Path(record.temporary_path)
        session.commit()
    with upload_lock(upload['id']):
        assert (await client.get(path, headers=headers)).status_code == 409
    assert (await client.get(path, headers=headers)).json()['state'] == 'open'
    completed = await post(client, '/upload-sessions/' + upload['id'] + '/complete', headers)
    assert not staging.exists()
    repeated = await post(client, '/upload-sessions/' + upload['id'] + '/complete', headers)
    assert repeated['artifact']['id'] == completed['artifact']['id']
    assert (await client.patch(path, headers={**headers, 'Upload-Offset': '0'}, content=content)).status_code == 409
    assert (await client.post('/api/v1/upload-sessions', headers=headers, json={**payload, 'sha256': '0' * 64})).status_code == 409


async def test_recovery_reuses_artifact_committed_before_session_completion(client, password_auth):
    _, admin = await bootstrap(client)
    _, _, run, _ = await secured_run(client, admin, 'Committed')
    content = b'committed before interruption'
    headers, payload, upload = await agent_upload(client, admin, run['id'], content, 'committed')
    artifact = await post(client, f'/runs/{run["id"]}/artifacts/upload', admin,
        files={'file': ('committed.raw', content)}, data={'metadata_json': '{"upload_session_id": "' + upload['id'] + '"}'})
    with SessionLocal() as session:
        record = session.get(UploadSession, upload['id'])
        record.state = UploadState.VERIFYING
        session.commit()
    recovered = await post(client, '/upload-sessions/' + upload['id'] + '/complete', headers)
    assert recovered['artifact']['id'] == artifact['id']
    with SessionLocal() as session:
        assert len(list(session.scalars(select(Artifact).where(Artifact.sha256 == payload['sha256'])))) == 1


async def test_backup_lock_allows_reads_and_rejects_writes_without_blocking(client, hierarchy):
    with maintenance_lock(get_settings().storage_root, exclusive=True):
        assert (await client.get('/api/v1/runs')).status_code == 200
        response = await client.post('/api/v1/projects', json={'name': 'During backup'})
        assert response.status_code == 503
        assert response.headers['retry-after'] == '10'
    assert (await client.post('/api/v1/projects', json={'name': 'After backup'})).status_code == 201


async def test_maintenance_preserves_active_uploads_and_referenced_objects(client, password_auth):
    import io
    import os

    _, admin = await bootstrap(client)
    _, _, run, artifact = await secured_run(client, admin, 'Sweeper')
    _, _, abandoned = await agent_upload(client, admin, run['id'], b'abandoned', 'abandoned')
    _, _, active = await agent_upload(client, admin, run['id'], b'active', 'active')
    storage = LocalArtifactStorage(get_settings().storage_root)
    old_orphan = storage.ingest_stream(io.BytesIO(b'old orphan'))
    recent_orphan = storage.ingest_stream(io.BytesIO(b'recent orphan'))
    old = time.time() - 2 * 86400
    os.utime(storage.resolve(old_orphan.key), (old, old))
    with SessionLocal() as session:
        abandoned_row = session.get(UploadSession, abandoned['id'])
        abandoned_row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        abandoned_path = Path(abandoned_row.temporary_path)
        active_path = Path(session.get(UploadSession, active['id']).temporary_path)
        source_path = storage.resolve(session.get(Artifact, artifact['id']).storage_key)
        os.utime(source_path, (old, old))
        session.commit()
    with maintenance_lock(storage.root, exclusive=False):
        assert sweep_storage() == {'uploads': 0, 'objects': 0}
    assert abandoned_path.exists()
    result = sweep_storage()
    assert result['uploads'] >= 1 and result['objects'] == 1
    assert not abandoned_path.exists()
    assert active_path.exists() and source_path.exists()
    assert storage.resolve(recent_orphan.key).exists()
    assert not storage.resolve(old_orphan.key).exists()
    # An expired session can also restart after its staging payload was reclaimed.
    with SessionLocal() as session:
        assert session.get(UploadSession, abandoned['id']).state == 'expired'


async def test_large_ingestion_does_not_block_health_requests(client, hierarchy, monkeypatch):
    import asyncio
    import threading

    started = threading.Event()
    release = threading.Event()
    original = LocalArtifactStorage.ingest_stream

    def slow_ingest(storage, stream):
        started.set()
        assert release.wait(5), 'Test did not release ingestion'
        return original(storage, stream)

    monkeypatch.setattr(LocalArtifactStorage, 'ingest_stream', slow_ingest)
    upload = asyncio.create_task(client.post(
        f'/api/v1/runs/{hierarchy["run_id"]}/artifacts/upload',
        files={'file': ('slow.raw', b'payload')},
    ))
    try:
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()
        response = await asyncio.wait_for(client.get('/health'), timeout=0.5)
        assert response.status_code == 200
    finally:
        release.set()
    assert (await upload).status_code == 201


async def test_restore_mode_rejects_mutation_and_disables_cleanup(client, hierarchy, monkeypatch):
    monkeypatch.setenv('SPECTARR_RESTORE_MODE', 'true')
    get_settings.cache_clear()
    try:
        assert (await client.get('/api/v1/runs')).status_code == 200
        response = await client.post('/api/v1/projects', json={'name': 'Unexpected restore mutation'})
        assert response.status_code == 503
        assert sweep_storage() == {'uploads': 0, 'objects': 0}
    finally:
        get_settings.cache_clear()


async def test_api_lifespan_initializes_profiles_and_stops_maintenance(monkeypatch):
    import spectarr.main as main
    import asyncio

    monkeypatch.setattr(main, 'run_migrations', lambda: None)
    completed = []
    monkeypatch.setattr(main, 'sweep_storage', lambda: completed.append(True))
    async with main.lifespan(main.app):
        for _ in range(100):
            if completed:
                break
            await asyncio.sleep(0.01)
        assert completed
    with SessionLocal() as session:
        assert session.scalar(select(Project.id)) is None


async def test_run_page_loads_related_data_with_bounded_query_count(client, hierarchy):
    from sqlalchemy import event
    from spectarr.database import engine
    from spectarr.models import ArtifactRole, ArtifactState, Job

    with SessionLocal() as session:
        for index in range(60):
            run = Run(experiment_id=hierarchy['experiment_id'], sample_id=hierarchy['sample_id'], name=f'Catalog {index}')
            artifact = Artifact(run=run, role=ArtifactRole.SOURCE, state=ArtifactState.READY,
                format='RAW', original_filename=f'{index}.raw', storage_key=f'catalog-{index}', byte_size=0, sha256='0' * 64)
            session.add(Job(kind='extract_metadata', input_artifact=artifact))
        session.commit()
    queries = []
    def record_query(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith('SELECT'):
            queries.append(statement)
    event.listen(engine, 'before_cursor_execute', record_query)
    try:
        response = await client.get('/api/v1/runs?page=true&limit=50')
    finally:
        event.remove(engine, 'before_cursor_execute', record_query)
    assert response.status_code == 200
    assert len(response.json()['items']) == 50
    assert len(queries) < 15
