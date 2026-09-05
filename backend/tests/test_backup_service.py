from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from spectarr.backup_service import BackupPolicy, BackupService, next_backup
from spectarr.config import Settings
from spectarr.database import Base
from spectarr.models import Artifact, Experiment, Project, Run
from spectarr.storage import LocalArtifactStorage


@pytest.fixture
def service(tmp_path):
    live = tmp_path / 'live'
    live.mkdir()
    destination = tmp_path / 'destination'
    destination.mkdir()
    settings = Settings(database_url=f'sqlite:///{live / "spectarr.db"}', storage_root=live / 'storage', backup_root=destination, backup_min_free_bytes=0)
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine)
    storage = LocalArtifactStorage(settings.storage_root)
    source = storage.ingest_stream(io.BytesIO(b'original acquisition'))
    with Session(engine) as session:
        project = Project(name='Backup study')
        experiment = Experiment(name='Experiment', project=project)
        run = Run(name='Acquisition', experiment=experiment)
        session.add(Artifact(run=run, role='source', state='ready', format='MGF', original_filename='test.mgf', storage_key=source.key, sha256=source.sha256, byte_size=source.byte_size))
        session.commit()
    engine.dispose()
    return BackupService(settings)


def quick_backup(service, monkeypatch):
    monkeypatch.setattr(service, '_restore', lambda _: service._update(last_restore_at=datetime.now(timezone.utc).isoformat()))
    service.request('backup')
    service.tick()
    status = service.status()
    assert status['status'] == 'complete', status['last_error']
    return status


def test_schedule_validation_and_next_slot():
    now = datetime(2026, 9, 4, 2, 0, tzinfo=timezone.utc)
    policy = BackupPolicy(enabled=True, every_days=7)
    assert next_backup(policy, now) == '2026-09-04T03:00:00+00:00'
    assert next_backup(policy, now.replace(hour=4)) == '2026-09-11T03:00:00+00:00'
    assert next_backup(BackupPolicy(), now) is None
    for values in ({'keep_last': 0}, {'time_utc': '25:00'}, {'every_days': 0}, {'restore_every_days': 0}):
        with pytest.raises(ValueError):
            BackupPolicy(**values)


def test_verified_backup_retention_and_namespaces(service, monkeypatch):
    service.configure(BackupPolicy(keep_last=1))
    first = quick_backup(service, monkeypatch)
    directory = service.settings.backup_root / f'spectarr-{first["instance_id"]}'
    unrelated = directory / 'my-files'
    unrelated.mkdir()
    (unrelated / 'keep').write_text('untouched')
    invalid = directory / 'backup-invalid'
    invalid.mkdir()
    (invalid / 'backup.json').write_text('broken metadata')
    second = quick_backup(service, monkeypatch)
    assert second['latest_backup'] != first['latest_backup']
    assert not (directory / first['latest_backup']).exists()
    assert (unrelated / 'keep').read_text() == 'untouched'
    assert invalid.exists()
    assert len(second['history']) == 1
    assert second['history'][0]['artifact_objects'] == 1
    assert second['last_success_at'] and second['same_filesystem']
    assert (directory / second['latest_backup'] / 'SHA256SUMS').is_file()
    service.request('backup')
    with pytest.raises(ValueError, match='already queued'):
        service.request('backup')


def test_backup_failure_never_prunes_last_good_copy(service, monkeypatch):
    first = quick_backup(service, monkeypatch)
    service.configure(BackupPolicy(keep_last=1))
    monkeypatch.setattr('spectarr.backup_service.create_backup_set', Mock(side_effect=OSError('disk full')))
    service.request('backup')
    service.tick()
    status = service.status()
    assert status['status'] == 'failed'
    assert status['last_success_at'] == first['last_success_at']
    assert status['last_error'] == 'disk full'
    assert status['history'][0]['id'] == first['latest_backup']
    assert not list(service.settings.backup_root.rglob('.partial-*'))


def test_destination_identity_and_overlap_fail_closed(service, monkeypatch):
    first = quick_backup(service, monkeypatch)
    marker = service.settings.backup_root / '.spectarr-backup-destination.json'
    original = marker.read_text()
    marker.unlink()
    assert not service.status()['destination_available']
    with pytest.raises(ValueError, match='marker is missing'):
        service.request('backup')
    assert not marker.exists()
    marker.write_text(json.dumps({'id': 'another destination'}))
    with pytest.raises(ValueError, match='different backup destination'):
        service.request('backup')
    marker.write_text(original)
    assert service.status()['history'][0]['id'] == first['latest_backup']
    service.settings.backup_root = service.settings.storage_root
    assert not service.status()['destination_available']
    with pytest.raises(ValueError, match='separate'):
        service.request('backup')
    service.settings.backup_root = None
    assert not service.status()['configured']


def test_scheduler_catches_up_once_and_reschedules_failures(service, monkeypatch):
    service.configure(BackupPolicy(enabled=True))
    service._update(next_backup_at=(datetime.now(timezone.utc) - timedelta(days=2)).isoformat())
    monkeypatch.setattr(service, '_space', Mock(side_effect=ValueError('Insufficient free space')))
    service.tick()
    state = service.status()
    assert state['status'] == 'failed'
    assert 'Insufficient free space' in state['last_error']
    assert datetime.fromisoformat(state['next_backup_at']) > datetime.now(timezone.utc)
    service.tick()
    assert service._space.call_count == 1
    service.configure(BackupPolicy(enabled=False))
    assert service.status()['next_backup_at'] is None


def test_running_operation_excludes_duplicates_and_restart_is_reported(service):
    service.request('backup')
    with service._lock('backup-operation.lock'):
        with pytest.raises(BlockingIOError):
            service.request('backup')
        service.tick()
    service._update(status='creating')
    service.tick()
    assert service.status()['status'] == 'interrupted'
    assert 'preserved' in service.status()['last_error']
    service.settings.restore_mode = True
    with pytest.raises(ValueError, match='disabled'):
        service.request('backup')
    service.tick()
    assert service.status()['restore_mode']


def test_restore_failure_preserves_existing_history(service, monkeypatch):
    first = quick_backup(service, monkeypatch)
    service.configure(BackupPolicy(keep_last=1))
    service._update(last_restore_at=None)
    monkeypatch.setattr(service, '_restore', Mock(side_effect=RuntimeError('Restored API unhealthy')))
    service.request('backup')
    service.tick()
    state = service.status()
    assert state['status'] == 'failed'
    assert len(state['history']) == 2
    assert any(item['id'] == first['latest_backup'] for item in state['history'])


def test_failed_manual_restore_requires_success_before_retention(service, monkeypatch):
    first = quick_backup(service, monkeypatch)
    service.configure(BackupPolicy(keep_last=1))
    monkeypatch.setattr(service, '_restore', BackupService._restore.__get__(service))
    probe = Mock(return_value=Mock(returncode=1, stderr='API unavailable'))
    monkeypatch.setattr('spectarr.backup_service.subprocess.run', probe)
    service.request('restore')
    service.tick()
    assert service.status()['last_restore_at'] == first['last_restore_at']
    assert 'API unavailable' in service.status()['last_restore_error']
    service.request('backup')
    service.tick()
    status = service.status()
    assert status['status'] == 'failed'
    assert len(status['history']) == 2
    assert any(item['id'] == first['latest_backup'] for item in status['history'])
    assert probe.call_count == 2
    probe.return_value = Mock(returncode=0)
    service.request('backup')
    service.tick()
    status = service.status()
    assert status['status'] == 'complete'
    assert status['last_restore_error'] is None
    assert len(status['history']) == 1
    assert probe.call_count == 3


def test_restore_cleanup_failure_remains_tracked_for_retry(service, monkeypatch):
    import tempfile
    from contextlib import contextmanager

    first = quick_backup(service, monkeypatch)
    namespace = service.settings.backup_root / f'spectarr-{first["instance_id"]}'
    temporary = namespace / '.restore-cleanup'
    original_temporary_directory = tempfile.TemporaryDirectory

    @contextmanager
    def failed_cleanup(**kwargs):
        if kwargs['prefix'] != '.restore-':
            with original_temporary_directory(**kwargs) as directory:
                yield directory
            return
        temporary.mkdir()
        yield str(temporary)
        raise OSError('cleanup unavailable')

    monkeypatch.setattr(service, '_restore', BackupService._restore.__get__(service))
    monkeypatch.setattr('spectarr.backup_service.tempfile.TemporaryDirectory', failed_cleanup)
    monkeypatch.setattr('spectarr.backup_service.subprocess.run', Mock(return_value=Mock(returncode=0)))
    service.request('restore')
    service.tick()
    assert service.status()['status'] == 'failed'
    assert service._load()['active_directory'] == temporary.name
    assert temporary.exists()
    service.tick()
    assert not temporary.exists()
    assert service._load()['active_directory'] is None
    assert service.status()['history'][0]['id'] == first['latest_backup']


@pytest.mark.timeout(45)
def test_real_restore_boots_isolated_api_and_detects_corruption(service):
    original_database = service.database.read_bytes()
    service.request('backup')
    service.tick()
    status = service.status()
    assert status['status'] == 'complete', status['last_error']
    assert status['last_restore_at']
    assert service.database.read_bytes() == original_database
    service.request('restore')
    service.tick()
    assert service.status()['status'] == 'complete'
    namespace = service.settings.backup_root / f'spectarr-{status["instance_id"]}'
    assert not list(namespace.glob('.restore-*'))
    archive = namespace / status['latest_backup'] / 'snapshot.tar'
    with archive.open('ab') as output:
        output.write(b'corruption')
    service.request('restore')
    service.tick()
    status = service.status()
    assert status['status'] == 'failed'
    assert 'checksum' in status['last_restore_error']


def test_interrupted_temporary_copy_is_cleaned_without_touching_snapshots(service, monkeypatch):
    first = quick_backup(service, monkeypatch)
    namespace = service.settings.backup_root / f'spectarr-{first["instance_id"]}'
    temporary = namespace / '.restore-abandoned'
    temporary.mkdir()
    (temporary / 'source').write_text('partial restore')
    temporary.chmod(0o500)
    service._update(status='restoring', active_directory=temporary.name)
    service.tick()
    assert not temporary.exists()
    assert service.status()['status'] == 'interrupted'
    assert service.status()['history'][0]['id'] == first['latest_backup']


def test_scheduled_success_survives_service_restart(service, monkeypatch):
    service.configure(BackupPolicy(enabled=True, keep_last=2))
    restarted = BackupService(service.settings)
    restarted._update(next_backup_at=(datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    monkeypatch.setattr(restarted, '_restore', lambda _: restarted._update(last_restore_at=datetime.now(timezone.utc).isoformat()))
    restarted.tick()
    status = service.status()
    assert status['status'] == 'complete'
    assert len(status['history']) == 1
    assert datetime.fromisoformat(status['next_backup_at']) > datetime.now(timezone.utc)


def test_space_and_history_validation(service, monkeypatch):
    first = quick_backup(service, monkeypatch)
    usage = Mock(free=0)
    monkeypatch.setattr('spectarr.backup_service.shutil.disk_usage', lambda _: usage)
    with pytest.raises(ValueError, match='Insufficient free space'):
        service._space(service.settings.backup_root, 1)
    namespace = service.settings.backup_root / f'spectarr-{first["instance_id"]}'
    invalid = namespace / ('backup-20260904T000000Z-' + 'a' * 32)
    invalid.mkdir()
    (invalid / 'backup.json').write_text('invalid')
    assert len(service.status()['history']) == 1


@pytest.mark.anyio
async def test_backup_api_controls_validate_and_require_admin(client, service, monkeypatch):
    monkeypatch.setattr('spectarr.backup_api.BackupService', lambda: service)
    status = await client.get('/api/v1/backups')
    assert status.status_code == 200
    assert not status.json()['policy']['enabled']
    bad = await client.put('/api/v1/backups/policy', json={'keep_last': 0})
    assert bad.status_code == 422
    configured = await client.put('/api/v1/backups/policy', json={'enabled': True, 'keep_last': 2})
    assert configured.status_code == 200
    assert configured.json()['next_backup_at']
    assert (await client.post('/api/v1/backups/restore-check')).status_code == 409
    queued = await client.post('/api/v1/backups/run')
    assert queued.status_code == 202
    assert queued.json()['status'] == 'queued'
    assert (await client.post('/api/v1/backups/run')).status_code == 409
    with service._lock('backup-operation.lock'):
        assert (await client.put('/api/v1/backups/policy', json={'enabled': False})).status_code == 409


@pytest.mark.anyio
async def test_backup_status_is_not_available_to_viewers(client, password_auth, monkeypatch):
    from spectarr.auth import hash_password
    from spectarr.database import SessionLocal
    from spectarr.models import User
    with SessionLocal() as session:
        session.add(User(username='viewer', password_hash=hash_password('viewer-password-123'), role='viewer', active=True))
        session.commit()
    response = await client.post('/api/v1/auth/login', json={'username': 'viewer', 'password': 'viewer-password-123'})
    assert response.status_code == 200
    headers = {'Authorization': f'Bearer {response.json()["access_token"]}'}
    for method, path in [('GET', '/backups'), ('POST', '/backups/run'), ('POST', '/backups/restore-check')]:
        assert (await client.request(method, '/api/v1' + path, headers=headers)).status_code == 403
