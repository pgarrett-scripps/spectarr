#!/usr/bin/env python3
"""Replay instrument writes through the real acquisition agent into an isolated Docker stack."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import request

ROOT = Path(__file__).resolve().parents[1]


def digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def file_manifest(path: Path) -> dict:
    files = sorted(path.rglob('*')) if path.is_dir() else [path]
    return {str(p.relative_to(path) if path.is_dir() else p.name):
            {'bytes': p.stat().st_size, 'sha256': digest(p)} for p in files if p.is_file()}


def wait(description, load, complete, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = load()
        if complete(value):
            return value
        time.sleep(0.15)
    raise RuntimeError(f'Timed out waiting for {description}')


class Api:
    def __init__(self, url):
        self.url = url

    def call(self, method, path, payload=None):
        body = json.dumps(payload).encode() if payload is not None else None
        req = request.Request(self.url + '/api/v1' + path, data=body, method=method,
                              headers={'Content-Type': 'application/json'})
        with request.urlopen(req, timeout=60) as response:
            content = response.read()
        return json.loads(content) if content else None


class FaultProxy:
    """Forward real HTTP requests and inject one reproducible transport failure."""

    def __init__(self, target_port):
        self.target_port = target_port
        self.offline = False
        self.next_fault = None
        self.triggered = threading.Event()
        self.release = threading.Event()
        self.events = []
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.forward()

            def do_POST(self):
                self.forward()

            def do_PATCH(self):
                self.forward()

            def forward(self):
                content = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                if owner.offline:
                    self.send_error(503, 'Simulated instrument network outage')
                    return
                connection = http.client.HTTPConnection('127.0.0.1', owner.target_port, timeout=60)
                try:
                    headers = {k: v for k, v in self.headers.items()
                               if k.lower() not in {'host', 'connection', 'content-length'}}
                    connection.request(self.command, self.path, content, headers)
                    response = connection.getresponse()
                    data = response.read()
                    response_headers = response.getheaders()
                    event = {'method': self.command, 'path': self.path, 'status': response.status,
                             'offset': self.headers.get('Upload-Offset'), 'bytes': len(content),
                             'time': time.monotonic()}
                    with owner.lock:
                        owner.events.append(event)
                        fault = None
                        if self.command == 'PATCH' and response.status < 300:
                            fault = owner.next_fault
                            owner.next_fault = None
                    if fault:
                        event['fault'] = fault
                        if fault == 'hold':
                            owner.offline = True
                        owner.triggered.set()
                        if fault == 'hold':
                            owner.release.wait(30)
                        self.close_connection = True
                        self.connection.shutdown(socket.SHUT_RDWR)
                        return
                    self.send_response(response.status)
                    for key, value in response_headers:
                        if key.lower() not in {'connection', 'transfer-encoding', 'content-length'}:
                            self.send_header(key, value)
                    self.send_header('Content-Length', str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (OSError, http.client.HTTPException):
                    self.close_connection = True
                finally:
                    connection.close()

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def arm(self, fault):
        self.triggered.clear()
        self.release.clear()
        self.next_fault = fault

    def close(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class AgentProcess:
    def __init__(self, root, proxy, credentials, *, dry_run=False):
        root.mkdir()
        self.root = root
        self.incoming = root / 'incoming'
        self.incoming.mkdir()
        self.db = root / 'queue.sqlite3'
        self.log_path = root / 'agent.log'
        self.log = self.log_path.open('ab')
        self.process = None
        config = {
            'server_url': f'http://127.0.0.1:{proxy.server.server_port}',
            'watch_paths': [str(self.incoming)], 'state_db': str(self.db),
            'agent_id': credentials['id'], 'agent_token': credentials['token'],
            'poll_interval_seconds': 0.15, 'stability_seconds': 1.2,
            'heartbeat_interval_seconds': 1, 'chunk_size_bytes': 65536,
            'retry_base_seconds': 0.5, 'retry_max_seconds': 2,
            'request_timeout_seconds': 10, 'dry_run': dry_run,
        }
        self.config_path = root / 'agent.toml'
        self.config_path.write_text('[agent]\n' + '\n'.join(
            f'{key} = {json.dumps(value)}' for key, value in config.items()) + '\n')
        self.config_path.chmod(0o600)
        self.start()

    def start(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith('SPECTARR_')}
        env['PYTHONPATH'] = str(ROOT / 'services/agent/src')
        self.process = subprocess.Popen(
            [sys.executable, '-m', 'spectarr_agent.cli', '--config', str(self.config_path)],
            env=env, stdout=self.log, stderr=subprocess.STDOUT)

    def rows(self):
        if not self.db.exists():
            return []
        with sqlite3.connect(f'{self.db.as_uri()}?mode=ro', uri=True) as connection:
            connection.row_factory = sqlite3.Row
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='upload_queue'").fetchone():
                return []
            return [dict(row) for row in connection.execute(
                'SELECT source_name, status, upload_id, artifact_id, attempts FROM upload_queue')]

    def alive(self):
        if self.process.poll() is not None:
            raise RuntimeError(f'Acquisition agent exited. See {self.log_path}')

    def completed(self, name):
        def load():
            self.alive()
            return [row for row in self.rows() if row['source_name'] == name
                    and row['status'] in {'complete', 'deduplicated'}]
        rows = wait(f'upload of {name}', load, bool, 180)
        assert len(rows) == 1, 'Duplicate upload queue entries'
        return rows[0]

    def stop(self, kill=False):
        if self.process and self.process.poll() is None:
            self.process.kill() if kill else self.process.terminate()
            self.process.wait(timeout=15)

    def close(self):
        self.stop()
        self.log.close()


def no_upload(agent, name, duration):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        agent.alive()
        assert not any(row['source_name'] == name for row in agent.rows()), f'Premature queueing of {name}'
        time.sleep(0.1)


def grow_file(agent, path, content):
    chunk = max(1, len(content) // 12)
    with path.open('wb') as stream:
        for offset in range(0, len(content), chunk):
            stream.write(content[offset:offset + chunk])
            stream.flush()
            no_upload(agent, path.name, 0.2)


def verify_artifact(api, row, source, library):
    artifact = api.call('GET', f"/artifacts/{row['artifact_id']}")
    assert artifact['state'] == 'ready' and artifact['role'] == 'source'
    access = api.call('GET', f"/artifacts/{artifact['id']}/access")
    assert access['availability'] == 'available'
    relative = Path(access['library_relative_path'])
    assert not relative.is_absolute() and '..' not in relative.parts
    stored = (library / relative).resolve()
    assert stored.is_relative_to(library.resolve())
    if source.is_dir():
        assert file_manifest(source) == file_manifest(stored)
        bundle = artifact['bundle_manifest']
        expected = {entry['path']: {'bytes': entry['size'], 'sha256': entry['sha256']}
                    for entry in bundle['files']}
        assert expected == file_manifest(source)
        assert hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(',', ':')).encode()).hexdigest() == artifact['sha256']
    else:
        assert digest(source) == digest(stored)
        assert digest(source) == artifact['sha256']
        with request.urlopen(api.url + f"/api/v1/artifacts/{artifact['id']}/download", timeout=60) as response:
            assert hashlib.sha256(response.read()).hexdigest() == artifact['sha256']
    run = api.call('GET', f"/runs/{artifact['run_id']}")
    assert run['assignment_status'] == 'needs_assignment', run['assignment_status']
    return artifact


def verify_spectra(api, artifact, expected=None):
    artifact_id = artifact['id']
    catalog = wait('spectrum catalog', lambda: api.call('GET', f'/artifacts/{artifact_id}/spectrum-catalog'),
                   lambda value: value['status'] in {'ready', 'failed'}, 600)
    assert catalog['status'] == 'ready', catalog
    assert catalog['spectrum_count'] > 0
    if expected is not None:
        assert catalog['spectrum_count'] == expected, catalog
    nonempty = False
    for level, sort, direction in ((1, 'scan_number', 'asc'), (2, 'scan_number', 'asc'),
                                   (2, 'scan_number', 'desc'), (1, 'peak_count', 'desc'),
                                   (2, 'peak_count', 'desc')):
        page = api.call('POST', f'/artifacts/{artifact_id}/spectra/query',
                        {'limit': 1, 'ms_levels': [level], 'sort': sort, 'direction': direction})
        if not page['items']:
            continue
        entry = page['items'][0]
        spectrum = api.call('GET', f"/artifacts/{artifact_id}/spectra/{entry['id']}")
        arrays = spectrum.get('arrays', {})
        assert isinstance(arrays.get('mz'), list) and isinstance(arrays.get('intensity'), list), 'Missing peak arrays'
        assert len(arrays['mz']) == len(arrays['intensity']), 'Invalid peak arrays'
        nonempty = nonempty or bool(arrays['mz'])
        assert len(arrays['mz']) == entry['peak_count'], 'Spectrum peaks differ from the cataloged record'
        assert spectrum['metadata']['native_id'] == entry['native_id'], 'Wrong native spectrum identity'
        assert spectrum['metadata']['ms_level'] == level, 'Wrong spectrum MS level'
        if entry.get('representation') in {'profile', 'centroid'}:
            assert spectrum['metadata']['spectrum_type'] == entry['representation'], 'Spectrum representation changed'
    assert nonempty, 'No sampled spectrum contains peaks'
    return catalog['spectrum_count']


def exercise(api, port, work, args, restart):
    assert not api.call('GET', '/runs?limit=1'), 'Rehearsal requires a fresh disposable catalog'
    report = {'status': 'running', 'scenarios': [], 'limitations': [],
              'harness_sha256': digest(Path(__file__)),
              'agent_sources': file_manifest(ROOT / 'services/agent/src/spectarr_agent')}
    report['agent_sources'] = {name: value for name, value in report['agent_sources'].items() if name.endswith('.py')}
    results = work / 'results.json'
    library = work / 'data/storage/library'
    fixtures = {name: file_manifest(path) for name, path in [('thermo', args.thermo), ('bruker', args.bruker)] if path}
    def record(name, **evidence):
        report['scenarios'].append({'name': name, 'status': 'passed', **evidence})
        results.write_text(json.dumps(report, indent=2) + '\n')
        print(f'PASS {name}', flush=True)
    with ExitStack() as cleanup:
        proxy = FaultProxy(port)
        cleanup.callback(proxy.close)
        credentials = api.call('POST', '/agents/register', {'name': 'Simulated instrument', 'capabilities': ['resumable_upload', 'bundle_upload']})
        agent = AgentProcess(work / 'instrument', proxy, credentials)
        cleanup.callback(agent.close)
        wait('agent startup', lambda: agent.db.exists(), bool)

        # This negative control never sends an incomplete acquisition to the server.
        dry = AgentProcess(work / 'pause-control', proxy, credentials, dry_run=True)
        cleanup.callback(dry.close)
        with (dry.incoming / 'unfinished.raw').open('wb') as writing:
            writing.write(b'Incomplete acquisition still open for writing')
            writing.flush()
            wait('inactivity limitation', lambda: dry.log_path.read_text(), lambda value: 'Dry run verified' in value, 15)
        dry.alive()
        assert not dry.rows()
        dry.stop()
        report['limitations'].append('An unmarked acquisition paused beyond the stability window is considered ready. This was reproduced in dry-run mode without uploading incomplete data.')
        record('long unmarked pause characterized without upload')

        template = (ROOT / 'examples/demo.mgf').read_bytes()
        content = b''.join(template.replace(b'Spectarr demo', f'Simulated acquisition {index}'.encode()) for index in range(600))
        mgf = agent.incoming / 'growing.mgf'
        proxy.arm('drop')
        grow_file(agent, mgf, content)
        row = agent.completed(mgf.name)
        assert proxy.triggered.is_set(), 'Lost response fault was not exercised'
        fault = next(event for event in proxy.events if event.get('fault') == 'drop')
        retries = [e for e in proxy.events if e['path'] == fault['path'] and e['offset'] == fault['offset']]
        assert len(retries) >= 2, 'Committed chunk was not retried'
        mgf_artifact = verify_artifact(api, row, mgf, library)
        assert digest(mgf) == hashlib.sha256(content).hexdigest(), 'Agent changed the producer file'
        record('growing file and lost committed chunk response', source_sha256=mgf_artifact['sha256'], retry_count=len(retries) - 1)

        resume = agent.incoming / ('resumed.raw' if args.thermo else 'resumed.mgf')
        source = args.thermo.read_bytes() if args.thermo else content.replace(b'Simulated', b'Resumed')
        marker = resume.with_name(resume.name + '.lock')
        marker.touch()
        resume.write_bytes(source[:len(source) // 2])
        no_upload(agent, resume.name, 2.5)
        resume.write_bytes(source)
        no_upload(agent, resume.name, 1.5)
        proxy.arm('hold')
        marker.unlink()
        assert proxy.triggered.wait(30), 'Crash fault was not reached'
        agent.stop(kill=True)
        crash_row = next(r for r in agent.rows() if r['source_name'] == resume.name)
        assert crash_row['status'] == 'uploading'
        proxy.release.set()
        agent.start()
        wait('offline retry after agent restart', agent.rows,
             lambda rows: any(r['source_name'] == resume.name and r['status'] == 'retry'
                              and r['attempts'] > crash_row['attempts'] for r in rows))
        proxy.offline = False
        row = agent.completed(resume.name)
        assert row['upload_id'] == crash_row['upload_id']
        patches = [e for e in proxy.events if row['upload_id'] in e['path'] and e['method'] == 'PATCH']
        assert int(patches[0]['offset']) == 0 and int(patches[1]['offset']) == 65536, patches[:2]
        raw_artifact = verify_artifact(api, row, resume, library)
        assert digest(resume) == hashlib.sha256(source).hexdigest(), 'Agent changed the producer file'
        record('lock marker, network outage and hard agent restart', resumed_offset=65536, same_upload_session=True,
               source_sha256=raw_artifact['sha256'])

        duplicate = agent.incoming / ('separate-acquisition.raw' if args.thermo else 'separate-acquisition.mgf')
        marker = duplicate.with_name(duplicate.name + '.lock')
        marker.touch()
        shutil.copyfile(resume, duplicate)
        marker.unlink()
        row = agent.completed(duplicate.name)
        other = verify_artifact(api, row, duplicate, library)
        assert other['run_id'] != raw_artifact['run_id'] and other['id'] != raw_artifact['id']
        assert other['sha256'] == raw_artifact['sha256'] and row['status'] == 'deduplicated'
        count = len(agent.rows())
        time.sleep(3)
        agent.alive()
        assert len(agent.rows()) == count
        record('distinct acquisitions with identical bytes and repeat polling', separate_runs=True, deduplicated_bytes=True)

        artifacts = [(mgf_artifact, 1200), (raw_artifact, args.thermo_spectra if args.thermo else 1200)]
        if args.bruker:
            bundle = agent.incoming / 'late-members.d'
            bundle.mkdir()
            marker = bundle / 'acquisition.lock'
            marker.touch()
            members = sorted(p for p in args.bruker.rglob('*') if p.is_file())
            for index, member in enumerate(members):
                destination = bundle / member.relative_to(args.bruker)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(member, destination)
                no_upload(agent, bundle.name, 1.5 if index == 0 else 0.2)
            no_upload(agent, bundle.name, 2)
            marker.unlink()
            row = agent.completed(bundle.name)
            artifact = verify_artifact(api, row, bundle, library)
            assert file_manifest(bundle) == fixtures['bruker'], 'Agent changed the producer bundle'
            artifacts.append((artifact, None))
            record('native directory with late members and long paused writes', member_count=len(members), bundle_sha256=artifact['sha256'])
        else:
            report['limitations'].append('Native vendor directory replay was not run. Supply --bruker to include it.')
        agent.stop()
        before = sorted(run['id'] for run in api.call('GET', '/runs?limit=500'))
        restart()
        agent.start()
        time.sleep(3)
        agent.alive()
        assert sorted(run['id'] for run in api.call('GET', '/runs?limit=500')) == before
        assert all(row['status'] in {'complete', 'deduplicated'} for row in agent.rows())
        agent.stop()
        record('server and agent restart preserve run identities')
        for artifact, expected in artifacts:
            count = verify_spectra(api, artifact, expected)
            record('spectrum extraction and retrieval', artifact_id=artifact['id'], spectra=count)
        for artifact, expected in artifacts:
            job = api.call('POST', f"/runs/{artifact['run_id']}/derivatives",
                           {'format': 'mzML', 'input_artifact_id': artifact['id']})
            job = wait('conversion', lambda: api.call('GET', f"/jobs/{job['id']}"),
                       lambda value: value['state'] in {'succeeded', 'failed', 'cancelled'}, 600)
            assert job['state'] == 'succeeded', job
            output = api.call('GET', f"/artifacts/{job['output_artifact_id']}")
            assert output['parent_artifact_id'] == artifact['id']
            count = verify_spectra(api, output, expected)
            access = api.call('GET', f"/artifacts/{output['id']}/access")
            converted = library / access['library_relative_path']
            import gzip
            from xml.etree import ElementTree

            xml_count = xml_peaks = 0
            opener = gzip.open if converted.suffix == '.gz' else open
            with opener(converted, 'rb') as stream:
                for _, element in ElementTree.iterparse(stream, events=['end']):
                    if element.tag.rsplit('}', 1)[-1] == 'spectrum':
                        xml_count += 1
                        xml_peaks += int(element.attrib['defaultArrayLength'])
                        element.clear()
            assert xml_count == count and xml_peaks > 0, (xml_count, count, xml_peaks)
            record('automatic upload through conversion and spectrum reading',
                   source_format=artifact['format'], spectra=count, xml_peaks=xml_peaks,
                   job_id=job['id'])
        for name, path in [('thermo', args.thermo), ('bruker', args.bruker)]:
            if path:
                assert file_manifest(path) == fixtures[name], 'Input fixture changed'
        assert len(api.call('GET', '/runs?limit=500')) == len(agent.rows())
        report['run_count'] = len(agent.rows())
        report['fixtures'] = fixtures
        report['http_events'] = proxy.events
        report['status'] = 'passed_with_documented_limitations'
        results.write_text(json.dumps(report, indent=2) + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--external-only', action='store_true', help='Run only external inventory scenarios')
    parser.add_argument('--image', default='spectarr-spectarr', help='Existing local image, resolved to an immutable ID')
    parser.add_argument('--thermo', type=Path, help='Optional real Thermo RAW fixture')
    parser.add_argument('--thermo-spectra', type=int, help='Optional independently known source spectrum count')
    parser.add_argument('--bruker', type=Path, help='Optional native .d fixture, including TimSim output')
    args = parser.parse_args()
    for field in ('thermo', 'bruker'):
        value = getattr(args, field)
        if value:
            assert not value.is_symlink()
            value = value.resolve(strict=True)
            if field == 'thermo':
                assert value.is_file()
            else:
                assert value.is_dir() and not any(p.is_symlink() for p in value.rglob('*'))
            setattr(args, field, value)
    work = Path(tempfile.mkdtemp(prefix='spectarr-instrument-rehearsal-'))
    print(f'Rehearsal evidence: {work}', flush=True)
    image = subprocess.check_output(['docker', 'image', 'inspect', '--format', '{{.Id}}', args.image], text=True).strip()
    ports = []
    with ExitStack() as sockets:
        for _ in range(2):
            sock = sockets.enter_context(socket.socket())
            sock.bind(('127.0.0.1', 0))
            ports.append(sock.getsockname()[1])
    (work / 'data').mkdir()
    (work / 'imports').mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SPECTARR_', 'COMPOSE_'))}
    env.update({'SPECTARR_IMAGE_REF': image, 'SPECTARR_DATA_DIR': str(work / 'data'),
                'SPECTARR_STORAGE_DIR': str(work / 'data/storage'), 'SPECTARR_IMPORT_DIR': str(work / 'imports'),
                'SPECTARR_AUTH_MODE': 'local', 'SPECTARR_BIND_ADDRESS': '127.0.0.1',
                'SPECTARR_JOB_LEASE_SECONDS': '30',
                'SPECTARR_UID': str(os.getuid()), 'SPECTARR_GID': str(os.getgid()),
                'SPECTARR_PORT': str(ports[0]), 'SPECTARR_MCP_PORT': str(ports[1]),
                'SPECTARR_REMOTE_IMPORTS_ENABLED': 'false'})
    compose = ['docker', 'compose', '--env-file', '/dev/null', '-p', work.name, '-f', str(ROOT / 'release/compose.yaml')]
    (work / 'IMAGE').write_text(image + '\n')
    try:
        subprocess.run([*compose, 'up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '120'], env=env, check=True)
        def restart():
            subprocess.run([*compose, 'restart'], env=env, check=True)
            subprocess.run([*compose, 'up', '-d', '--pull', 'never', '--wait', '--wait-timeout', '120'], env=env, check=True)
        api = Api(f'http://127.0.0.1:{ports[0]}')
        if not args.external_only:
            exercise(api, ports[0], work, args, restart)
        from external_rehearsal import exercise as exercise_external
        exercise_external(api, work, image, restart, f"http://127.0.0.1:{ports[1]}/mcp")
    except BaseException as exc:
        result_path = work / 'results.json'
        report = json.loads(result_path.read_text()) if result_path.exists() else {}
        report['status'] = 'failed'
        report['failure'] = f'{type(exc).__name__}: {exc}'
        result_path.write_text(json.dumps(report, indent=2) + '\n')
        (work / 'failure.txt').write_text(f'{type(exc).__name__}: {exc}\n')
        raise
    finally:
        with (work / 'container.log').open('w') as log:
            subprocess.run([*compose, 'logs', '--no-color'], env=env, stdout=log, stderr=subprocess.STDOUT)
        subprocess.run([*compose, 'down', '--timeout', '15'], env=env, check=True)
        print(f'Retained evidence and test data: {work}', flush=True)


if __name__ == '__main__':
    main()
