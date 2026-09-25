import hashlib

import pytest
from sqlalchemy import select, func

from spectarr.database import SessionLocal
from spectarr.models import Artifact, ExternalEntry, ExternalObservation, ExternalRevision, Run
from spectarr.external_api import manifest_identity
from spectarr.schemas import BundleUploadManifest

pytestmark = pytest.mark.anyio
IDENTITY = "posix:123:456"
CONTENT = b"BEGIN IONS\n100 50\nEND IONS\n"
SHA = hashlib.sha256(CONTENT).hexdigest()


async def post(client, path, body, headers=None, expected=200):
    response = await client.post('/api/v1' + path, json=body, headers=headers)
    assert response.status_code == expected, response.text
    return response.json()


async def setup(client, hierarchy, name="archive"):
    agent = await post(client, '/agents/register', {"name": name, "capabilities": ["external_inventory_v1"]}, expected=201)
    token = {"Authorization": "Bearer " + agent['token']}
    root = await post(client, '/external-roots', {"project_id": hierarchy['project_id'], "agent_id": agent['id'], "label": name, "path": '/archive/' + name}, expected=201)
    return root, token


async def task(client, root, kind="scan", **kwargs):
    return await post(client, f"/external-roots/{root['id']}/tasks", {"kind": kind, **kwargs}, expected=201)


def fact(name='one.mgf', signature='sig', kind='file'):
    return {"path": name, "signature": signature, "kind": kind, "format": "MGF" if kind == 'file' else 'vendor_directory', "byte_size": len(CONTENT)}


async def scan(client, root, headers, files, status='complete'):
    t = await task(client, root)
    await post(client, f"/external-agent/tasks/{t['id']}/observations", {"sequence": 0, "identity": IDENTITY, "files": files}, headers)
    await post(client, f"/external-agent/tasks/{t['id']}/result", {"status": status, "identity": IDENTITY}, headers)
    page = (await client.get('/api/v1/external-entries', params={"project_id": root['project_id']})).json()
    return page['items']


async def verify(client, root, headers, loc, content=CONTENT, manifest=None):
    t = await task(client, root, 'verify', location_id=loc['id'])
    checksum = manifest_identity(BundleUploadManifest(**manifest))[0] if manifest else hashlib.sha256(content).hexdigest()
    await post(client, f"/external-agent/tasks/{t['id']}/result", {"status": "complete", "identity": IDENTITY, "signature": "verified", "sha256": checksum, "byte_size": len(content), "manifest": manifest, "readiness": "stable_by_observation"}, headers)
    entries = (await client.get('/api/v1/external-entries', params={"project_id": root['project_id']})).json()['items']
    return next(location for e in entries for location in e['locations'] if location['id'] == loc['id'])


async def test_inventory_never_creates_managed_files_and_paginates(client, hierarchy):
    root, headers = await setup(client, hierarchy)
    entries = await scan(client, root, headers, [fact('one_100%.mgf'), fact('two.mgf')])
    assert len(entries) == 2
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Run)) == 1
        assert db.scalar(select(func.count()).select_from(Artifact)) == 0
    first = (await client.get('/api/v1/external-entries', params={'project_id': root['project_id'], 'limit': 1})).json()
    second = (await client.get('/api/v1/external-entries', params={'project_id': root['project_id'], 'limit': 1, 'after': first['next_cursor']})).json()
    assert first['items'][0]['id'] != second['items'][0]['id']
    assert second['next_cursor'] is None
    literal = (await client.get('/api/v1/external-entries', params={'project_id': root['project_id'], 'query': '_100%'})).json()
    assert literal['total'] == 1
    access = (await client.get('/api/v1/external-entries/' + entries[0]['id'] + '/access')).json()
    assert access['storage_mode'] == 'external'
    assert access['locations'][0]['path_scope'] == 'agent_host'
    assert access['locations'][0]['mapping_required'] is True
    assert 'download_url' not in access
    assert 'server_path' not in access
    assert (await client.get('/api/v1/external-entries', params={'project_id': root['project_id']}, headers=headers)).status_code == 403


@pytest.mark.parametrize('status', ['partial', 'unavailable', 'failed'])
async def test_failed_scan_cannot_mark_entries_missing(client, hierarchy, status):
    root, headers = await setup(client, hierarchy)
    entries = await scan(client, root, headers, [fact()])
    await scan(client, root, headers, [], status)
    access = (await client.get('/api/v1/external-entries/' + entries[0]['id'] + '/access')).json()
    assert access['locations'][0]['status'] == 'observed'
    assert access['locations'][0]['root_status'] in {'partial', 'unavailable'}
    await scan(client, root, headers, [])
    access = (await client.get('/api/v1/external-entries/' + entries[0]['id'] + '/access')).json()
    assert access['locations'][0]['status'] == 'not_found'


async def test_scan_protocol_rejects_reordering_wrong_agents_and_identity_change(client, hierarchy):
    root, headers = await setup(client, hierarchy)
    _, foreign = await setup(client, hierarchy, 'other')
    t = await task(client, root)
    path = f"/external-agent/tasks/{t['id']}/observations"
    payload = {'sequence': 0, 'identity': IDENTITY, 'files': [fact()]}
    await post(client, path, payload, foreign, expected=403)
    await post(client, path, {**payload, 'sequence': 1}, headers, expected=409)
    await post(client, path, payload, headers)
    await post(client, path, payload, headers)
    await post(client, path, {**payload, 'files': [fact('different.mgf')]}, headers, expected=409)
    await post(client, path, {**payload, 'sequence': 1, 'identity': 'different-drive'}, headers, expected=409)
    await post(client, f"/external-agent/tasks/{t['id']}/result", {'status': 'complete', 'identity': IDENTITY}, headers)
    await post(client, f"/external-agent/tasks/{t['id']}/result", {'status': 'complete', 'identity': IDENTITY}, headers)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ExternalObservation)) == 1


async def test_verification_changed_content_and_import_pinning(client, hierarchy):
    root, headers = await setup(client, hierarchy)
    entries = await scan(client, root, headers, [fact()])
    loc = await verify(client, root, headers, entries[0]['locations'][0])
    t = await task(client, root, 'import', location_id=loc['id'], revision_id=loc['revision_id'], experiment_id=hierarchy['experiment_id'])
    payload = {'filename': 'one.mgf', 'format': 'MGF', 'total_size': len(CONTENT), 'sha256': SHA,
        'run': {'name': 'Imported external', 'experiment_id': hierarchy['experiment_id'], 'source_class': 'spectrum_list'},
        'metadata_json': {'external_task_id': t['id']}}
    upload_headers = {**headers, 'Idempotency-Key': 'external-import:' + t['id']}
    await post(client, '/upload-sessions', {**payload, 'sha256': 'a' * 64}, upload_headers, expected=409)
    upload = await post(client, '/upload-sessions', payload, upload_headers, expected=201)
    assert (await client.patch('/api/v1/upload-sessions/' + upload['id'], content=CONTENT, headers={**headers, 'Upload-Offset': '0', 'Content-Type': 'application/octet-stream'})).status_code == 204
    artifact = (await post(client, '/upload-sessions/' + upload['id'] + '/complete', {}, headers))['artifact']
    await post(client, f"/external-agent/tasks/{t['id']}/result", {'status': 'complete', 'identity': IDENTITY, 'artifact_id': artifact['id']}, headers)
    history = (await client.get('/api/v1/external-entries/' + entries[0]['id'] + '/history')).json()
    assert any(o['facts'].get('artifact_id') == artifact['id'] for o in history['items'])
    changed = await verify(client, root, headers, loc, content=b'changed')
    assert changed['revision_id'] != loc['revision_id']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ExternalRevision)) == 2
        assert db.get(Artifact, artifact['id']).sha256 == SHA


async def test_bundle_rename_matching_and_explicit_relocation(client, hierarchy):
    root, headers = await setup(client, hierarchy)
    entries = await scan(client, root, headers, [fact('old.d', kind='bundle')])
    manifest = {'root_name': 'old.d', 'files': [{'path': 'analysis.tdf', 'size': len(CONTENT), 'sha256': SHA}]}
    old = await verify(client, root, headers, entries[0]['locations'][0], manifest=manifest)
    old_id = entries[0]['id']
    entries = await scan(client, root, headers, [fact('new.d', kind='bundle')])
    candidate = next(e for e in entries if e['name'] == 'new.d')
    new = await verify(client, root, headers, candidate['locations'][0], manifest={**manifest, 'root_name': 'new.d'})
    assert new['sha256'] != old['sha256']
    matches = (await client.get('/api/v1/external-entries/' + old_id + '/matches')).json()
    assert len(matches['items']) == 1
    payload = {'source_location_id': old['id'], 'candidate_location_id': new['id'], 'source_revision_id': old['revision_id'], 'candidate_revision_id': new['revision_id']}
    await post(client, '/external-relocations', {**payload, 'candidate_revision_id': 'wrong'}, expected=409)
    await post(client, '/external-relocations', payload)
    access = (await client.get('/api/v1/external-entries/' + old_id + '/access')).json()
    assert len(access['locations']) == 2
    assert {location['status'] for location in access['locations']} == {'observed', 'not_found'}
    with SessionLocal() as db:
        assert db.get(ExternalEntry, candidate['id']).alias_id == old_id


async def test_root_overlap_pause_cancel_and_unverified_import(client, hierarchy):
    root, headers = await setup(client, hierarchy)
    await post(client, '/external-roots', {'project_id': root['project_id'], 'agent_id': root['agent_id'], 'label': 'nested', 'path': root['path'] + '/nested'}, expected=409)
    await post(client, '/external-roots', {'project_id': root['project_id'], 'agent_id': root['agent_id'], 'label': 'unsafe', 'path': '/archive/../etc'}, expected=422)
    entries = await scan(client, root, headers, [fact()])
    await post(client, f"/external-roots/{root['id']}/tasks", {'kind': 'import', 'location_id': entries[0]['locations'][0]['id'], 'revision_id': 'missing', 'experiment_id': hierarchy['experiment_id']}, expected=404)
    t = await task(client, root)
    await post(client, f"/external-roots/{root['id']}/tasks", {'kind': 'scan'}, expected=409)
    await post(client, '/external-tasks/' + t['id'] + '/cancel', {})
    assert (await client.get('/api/v1/external-agent/tasks', headers=headers)).json()['items'] == []
    response = await client.patch('/api/v1/external-roots/' + root['id'], json={'enabled': False, 'revalidate': True})
    assert response.status_code == 200
    await post(client, f"/external-roots/{root['id']}/tasks", {'kind': 'scan'}, expected=409)


@pytest.mark.parametrize('name', ['../escape.mgf', '/absolute.mgf', 'a//b.mgf', 'a\\b.mgf'])
async def test_reports_reject_unsafe_paths(client, hierarchy, name):
    root, headers = await setup(client, hierarchy)
    t = await task(client, root)
    await post(client, f"/external-agent/tasks/{t['id']}/observations", {'sequence': 0, 'identity': IDENTITY, 'files': [fact(name)]}, headers, expected=422)


async def test_project_readers_cannot_see_another_inventory_or_request_work(client, password_auth):
    from test_platform import bootstrap
    from test_reliability import secured_run
    _, admin = await bootstrap(client)
    project, experiment, _, _ = await secured_run(client, admin, 'private archive')
    agent = await post(client, '/agents/register', {'name': 'private-agent'}, admin, expected=201)
    root = await post(client, '/external-roots', {'project_id': project['id'], 'agent_id': agent['id'], 'label': 'private', 'path': '/private/archive'}, admin, expected=201)
    t = await post(client, f"/external-roots/{root['id']}/tasks", {'kind': 'scan'}, admin, expected=201)
    agent_headers = {'Authorization': 'Bearer ' + agent['token']}
    await post(client, f"/external-agent/tasks/{t['id']}/observations", {'sequence': 0, 'identity': IDENTITY, 'files': [fact()]}, agent_headers)
    await post(client, f"/external-agent/tasks/{t['id']}/result", {'status': 'complete', 'identity': IDENTITY}, agent_headers)
    entry_id = (await client.get('/api/v1/external-entries', params={'project_id': project['id']}, headers=admin)).json()['items'][0]['id']
    user = await post(client, '/users', {'username': 'inventory-reader', 'password': 'inventory-test-password', 'role': 'viewer'}, admin, expected=201)
    login = await post(client, '/auth/login', {'username': 'inventory-reader', 'password': 'inventory-test-password'})
    viewer = {'Authorization': 'Bearer ' + login['access_token']}
    for url in [f"/external-roots?project_id={project['id']}", f"/external-entries?project_id={project['id']}",
                f"/external-entries/{entry_id}/access", f"/external-entries/{entry_id}/history", f"/external-entries/{entry_id}/matches", f"/external-roots/{root['id']}/tasks"]:
        assert (await client.get('/api/v1' + url, headers=viewer)).status_code == 403
    await post(client, f"/projects/{project['id']}/memberships", {'user_id': user['id'], 'role': 'viewer'}, admin, expected=201)
    assert (await client.get('/api/v1/external-entries/' + entry_id + '/access', headers=viewer)).status_code == 200
    await post(client, f"/external-roots/{root['id']}/tasks", {'kind': 'scan'}, viewer, expected=403)


async def test_changed_observation_invalidates_verification_without_erasing_history(client, hierarchy):
    root, headers = await setup(client, hierarchy)
    entries = await scan(client, root, headers, [fact()])
    loc = await verify(client, root, headers, entries[0]['locations'][0])
    entries = await scan(client, root, headers, [fact(signature='changed')])
    assert entries[0]['locations'][0]['revision_id'] is None
    with SessionLocal() as db:
        assert db.get(ExternalRevision, loc['revision_id']) is not None
    t = await task(client, root, 'verify', location_id=loc['id'])
    await post(client, f"/external-agent/tasks/{t['id']}/result", {'status': 'complete', 'identity': IDENTITY}, headers, expected=422)
    await post(client, f"/external-agent/tasks/{t['id']}/result", {'status': 'failed', 'error': 'changed during read'}, headers)


async def test_backup_contains_inventory_but_not_external_bytes_and_restore_pauses_work(client, hierarchy, tmp_path):
    import io
    import sqlite3
    from contextlib import closing
    import tarfile
    from pathlib import Path
    from spectarr.backup import create_backup_set
    from spectarr.config import get_settings
    from spectarr.external_api import reset_restored_inventory
    from spectarr.models import ExternalRoot, ExternalTask

    root, headers = await setup(client, hierarchy)
    await scan(client, root, headers, [fact()])
    t = await task(client, root)
    source = tmp_path / 'source-that-is-not-managed.raw'
    source.write_bytes(b'outside data')
    settings = get_settings()
    database = Path(settings.database_url.removeprefix('sqlite:///'))
    stream = io.BytesIO()
    create_backup_set(database.parent, stream, database_path=database, storage_root=settings.storage_root)
    stream.seek(0)
    with tarfile.open(fileobj=stream) as archive:
        assert not any(source.name in name for name in archive.getnames())
        copied = tmp_path / 'restored.db'
        copied.write_bytes(archive.extractfile('database.sqlite3').read())
    with closing(sqlite3.connect(copied)) as restored, restored:
        assert restored.execute('SELECT COUNT(*) FROM external_entries').fetchone()[0] == 1
        assert restored.execute('SELECT id FROM external_roots').fetchone()[0] == root['id']
    with SessionLocal() as db:
        reset_restored_inventory(db)
        assert db.get(ExternalRoot, root['id']).enabled is False
        assert db.get(ExternalRoot, root['id']).last_seen_at is None
        assert db.get(ExternalTask, t['id']).state == 'canceled'
    assert source.read_bytes() == b'outside data'


async def test_task_request_retries_keep_one_operation(client, hierarchy):
    root, headers = await setup(client, hierarchy)
    path = f"/external-roots/{root['id']}/tasks"
    request_headers = {'Idempotency-Key': 'stable-browser-request'}
    t = await post(client, path, {'kind': 'scan'}, request_headers, expected=201)
    repeated = await post(client, path, {'kind': 'scan'}, request_headers, expected=201)
    assert t['id'] == repeated['id']
    await post(client, '/external-tasks/' + t['id'] + '/cancel', {})
    repeated = await post(client, path, {'kind': 'scan'}, request_headers, expected=201)
    assert repeated['id'] == t['id'] and repeated['state'] == 'canceled'
    await post(client, path, {'kind': 'verify', 'location_id': 'different'}, request_headers, expected=409)
