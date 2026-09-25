"""Exercise external discovery through a real server and a read-only Docker mount."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def exercise(api, work, image, restart, mcp_url=None):
    archive = work / 'external-archive'
    archive.mkdir()
    state = work / 'external-agent'
    state.mkdir(mode=0o700)
    source = archive / 'original.mgf'
    content = b'BEGIN IONS\nTITLE=external-test\nPEPMASS=500\nCHARGE=2+\n100 50\n200 100\nEND IONS\n'
    source.write_bytes(content)
    checksum = hashlib.sha256(content).hexdigest()
    bundle = archive / 'original.d'
    bundle.mkdir()
    (bundle / 'analysis.tdf').write_bytes(b'fixture for inventory only')
    (bundle / 'Z.bin').write_bytes(b'upper')
    (bundle / 'z.bin').write_bytes(b'lower')
    project = api.call('POST', '/projects', {'name': 'External inventory rehearsal'})
    experiment = api.call('POST', '/experiments', {'name': 'Selected imports', 'project_id': project['id']})
    registration = api.call('POST', '/agents/register', {'name': 'External rehearsal agent', 'capabilities': ['external_inventory_v1']})
    root = api.call('POST', '/external-roots', {'label': 'Read-only test archive', 'path': '/archive', 'project_id': project['id'], 'agent_id': registration['id']})
    config = state / 'agent.toml'
    config.write_text('\n'.join([
        '[agent]', f'server_url = {json.dumps(api.url)}', 'mode = "catalog"',
        'watch_paths = ["/archive"]', 'state_db = "/state/queue.db"',
        f'agent_id = {json.dumps(registration["id"])}', f'agent_token = {json.dumps(registration["token"])}',
        'stability_seconds = 0', 'poll_interval_seconds = 1', 'heartbeat_interval_seconds = 1',
    ]) + '\n')
    config.chmod(0o600)
    command = ['docker', 'run', '--rm', '--network', 'host', '--user', f'{os.getuid()}:{os.getgid()}',
               '--mount', f'type=bind,source={archive},target=/archive,readonly',
               '--mount', f'type=bind,source={state},target=/state',
               '--mount', f'type=bind,source={ROOT / "services/agent/src"},target=/agent-src,readonly',
               '-e', 'PYTHONPATH=/agent-src', '--entrypoint', 'python', image,
               '-m', 'spectarr_agent.cli', '--config', '/state/agent.toml', '--server-url', api.url, '--once']
    def agent():
        with (work / 'external-agent.log').open('a') as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT, timeout=90)
    def entries():
        return api.call('GET', '/external-entries?project_id=' + project['id'])['items']
    def queue(kind, **fields):
        task = api.call('POST', f"/external-roots/{root['id']}/tasks", {'kind': kind, **fields})
        agent()
        outcome = next(t for t in api.call('GET', f"/external-roots/{root['id']}/tasks")['items'] if t['id'] == task['id'])
        assert outcome['state'] == 'complete', outcome
        return outcome
    def find(name):
        return next(e for e in entries() if e['name'] == name)
    def verify(name):
        entry = find(name)
        location = next(loc for loc in entry['locations'] if loc['status'] == 'observed')
        queue('verify', location_id=location['id'])
        return find(name)['locations'][0]
    results = []
    def passed(name):
        results.append(name)
        print('External PASS: ' + name, flush=True)
        (work / 'external-results.json').write_text(json.dumps({'status': 'running', 'passed': results}, indent=2) + '\n')
    before = len(api.call('GET', '/runs?limit=500'))
    queue('scan')
    assert len(entries()) == 2
    assert len(api.call('GET', '/runs?limit=500')) == before
    assert source.read_bytes() == content
    passed('read-only source mount creates inventory without managed runs')
    if mcp_url:
        verify_mcp(api, project['id'], root['id'], mcp_url)
        passed('MCP searches and resolves external entries without creating work')
    old = verify('original.mgf')
    old_bundle = verify('original.d')
    assert old['sha256'] == checksum
    source.rename(archive / 'renamed.mgf')
    bundle.rename(archive / 'renamed.d')
    queue('scan')
    assert find('original.mgf')['locations'][0]['status'] == 'not_found'
    renamed = verify('renamed.mgf')
    renamed_bundle = verify('renamed.d')
    for old_loc, new_loc in [(old, renamed), (old_bundle, renamed_bundle)]:
        api.call('POST', '/external-relocations', {'source_location_id': old_loc['id'], 'candidate_location_id': new_loc['id'],
                 'source_revision_id': old_loc['revision_id'], 'candidate_revision_id': new_loc['revision_id']})
    assert len(entries()) == 2
    assert old_bundle['sha256'] != renamed_bundle['sha256']
    passed('verified single-file and mixed-case bundle relocations preserve history')
    old_id = find('original.mgf')['id']
    imported = queue('import', location_id=renamed['id'], revision_id=renamed['revision_id'], experiment_id=experiment['id'])
    artifact = api.call('GET', '/artifacts/' + imported['artifact_id'])
    assert artifact['sha256'] == checksum
    assert artifact['metadata_json']['external_task_id'] == imported['id']
    assert (archive / 'renamed.mgf').read_bytes() == content
    passed('pinned import uses resumable upload and records provenance')
    original_stat = (archive / 'renamed.mgf').stat()
    (archive / 'renamed.mgf').write_bytes(content.replace(b'100 50', b'100 60'))
    os.utime(archive / 'renamed.mgf', ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    task = api.call('POST', f"/external-roots/{root['id']}/tasks", {'kind': 'import', 'location_id': renamed['id'], 'revision_id': renamed['revision_id'], 'experiment_id': experiment['id']})
    agent()
    failed = next(t for t in api.call('GET', f"/external-roots/{root['id']}/tasks")['items'] if t['id'] == task['id'])
    assert failed['state'] == 'failed'
    assert len(api.call('GET', '/runs?limit=500')) == before + 1
    passed('same-size edit with preserved mtime cannot replace a pinned import')
    (archive / 'renamed.mgf').write_bytes(content)
    # Permission failure must preserve the prior location and content history.
    archive.chmod(0o000)
    try:
        task = api.call('POST', f"/external-roots/{root['id']}/tasks", {'kind': 'scan'})
        agent()
    finally:
        archive.chmod(0o755)
    assert find('original.mgf')['locations'][1]['status'] in {'observed', 'not_found'}
    assert api.call('GET', '/external-roots?project_id=' + project['id'])['items'][0]['status'] == 'unavailable'
    passed('unreadable root preserves inventory without false deletion')
    restart()
    queue('scan')
    assert find('original.mgf')['id'] == old_id
    passed('server and agent restart retain external identities')
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        detail = api.call('GET', '/runs/' + artifact['run_id'])
        if detail.get('status') == 'ready':
            break
        time.sleep(.5)
    (work / 'external-results.json').write_text(json.dumps({'status': 'passed', 'passed': results,
        'project_id': project['id'], 'source_sha256': checksum, 'image': image,
        'scope': 'Linux local filesystem and read-only bind mount'}, indent=2) + '\n')


def verify_mcp(api, project_id, root_id, url):
    def rpc(method, params):
        body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
        req = Request(url, data=body, headers={'Content-Type': 'application/json'})
        with urlopen(req, timeout=30) as response:
            payload = json.load(response)
        assert 'error' not in payload, payload
        return payload['result']
    def tool(name, **args):
        result = rpc('tools/call', {'name': name, 'arguments': args})
        assert not result.get('isError'), result
        return json.loads(result['content'][0]['text'])
    before = api.call('GET', '/external-roots/' + root_id + '/tasks')
    page = tool('search_external_entries', project_id=project_id, limit=1)
    assert page['total'] > 0
    entry = tool('resolve_external_entry', entry_id=page['items'][0]['id'])
    assert entry['storage_mode'] == 'external'
    assert entry['locations'][0]['path_scope'] == 'agent_host'
    assert entry['locations'][0]['mapping_required'] is True
    assert 'download_url' not in entry
    assert api.call('GET', '/external-roots/' + root_id + '/tasks') == before
    assert all(t['annotations']['readOnlyHint'] for t in rpc('tools/list', {})['tools'])
