from __future__ import annotations

import io
import json
import sqlite3
import tarfile

import pytest

from spectarr.backup import create_backup_set, verify_backup_set, verify_objects
from spectarr.locking import maintenance_lock
from spectarr.runtime import process_commands
from spectarr.storage import LocalArtifactStorage


def snapshot_fixture(tmp_path):
    storage = LocalArtifactStorage(tmp_path / 'storage')
    source = storage.ingest_stream(io.BytesIO(b'original acquisition'))
    bundle_source = tmp_path / 'vendor.d'
    bundle_source.mkdir()
    (bundle_source / 'analysis.bin').write_bytes(b'vendor data')
    bundle = storage.ingest_path(bundle_source)
    database = tmp_path / 'spectarr.db'
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE artifacts (storage_key TEXT, sha256 TEXT, byte_size INTEGER, bundle_manifest TEXT, state TEXT)')
        connection.executemany('INSERT INTO artifacts VALUES (?, ?, ?, ?, ?)', [
            (source.key, source.sha256, source.byte_size, 'null', 'ready'),
            (bundle.key, bundle.sha256, bundle.byte_size, json.dumps(bundle.manifest), 'ready'),
        ])
    output = io.BytesIO()
    create_backup_set(tmp_path, output)
    return database, storage, source, output.getvalue()


def rewrite_archive(content, skip=None, corrupt=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(content)) as source, tarfile.open(fileobj=output, mode='w') as target:
        for member in source:
            if member.name == skip:
                continue
            payload = source.extractfile(member) if member.isfile() else None
            if member.name == corrupt:
                data = payload.read()
                payload = io.BytesIO(b'x' * len(data))
            target.addfile(member, payload)
    return output.getvalue()


def test_snapshot_verifies_files_and_vendor_bundle_members(tmp_path):
    database, storage, source, content = snapshot_fixture(tmp_path)
    assert verify_backup_set(io.BytesIO(content)) == 3
    assert verify_objects(database, storage.root) == 3
    for changed in (
        rewrite_archive(content, skip='storage/' + source.key),
        rewrite_archive(content, corrupt='storage/' + source.key),
    ):
        with pytest.raises(RuntimeError, match='Missing or corrupt'):
            verify_backup_set(io.BytesIO(changed))
    storage.resolve(source.key).unlink()
    with pytest.raises(RuntimeError, match='Missing or unsafe'):
        verify_objects(database, storage.root)
    with pytest.raises(RuntimeError, match='Missing or unsafe'):
        create_backup_set(tmp_path, io.BytesIO())


def test_backup_rejects_corrupt_bundle_members_even_with_valid_database(tmp_path):
    _, _, _, content = snapshot_fixture(tmp_path)
    with tarfile.open(fileobj=io.BytesIO(content)) as archive:
        member = next(name for name in archive.getnames() if name.endswith('/analysis.bin'))
    with pytest.raises(RuntimeError, match='Missing or corrupt'):
        verify_backup_set(io.BytesIO(rewrite_archive(content, corrupt=member)))


def test_backup_verification_supports_legacy_envelope(tmp_path):
    database, storage, _, _ = snapshot_fixture(tmp_path)
    storage_archive = io.BytesIO()
    with tarfile.open(fileobj=storage_archive, mode='w') as archive:
        archive.add(storage.root, arcname='storage')
    envelope = io.BytesIO()
    with tarfile.open(fileobj=envelope, mode='w') as archive:
        archive.add(database, arcname='database.sqlite3')
        member = tarfile.TarInfo('storage.tar')
        member.size = len(storage_archive.getvalue())
        archive.addfile(member, io.BytesIO(storage_archive.getvalue()))
    assert verify_backup_set(io.BytesIO(envelope.getvalue())) == 3


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'storage/../../escape'])
def test_backup_rejects_unsafe_member_names(name):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as archive:
        archive.addfile(tarfile.TarInfo(name), io.BytesIO())
    with pytest.raises(RuntimeError, match='Unsafe'):
        verify_backup_set(io.BytesIO(output.getvalue()))


def test_restore_mode_starts_no_processing_or_outbound_workers(monkeypatch):
    monkeypatch.setenv('SPECTARR_RESTORE_MODE', 'true')
    assert [name for name, _, _ in process_commands()] == ['spectrum reader', 'API']


def test_backup_lock_is_exclusive_across_descriptors(tmp_path):
    with maintenance_lock(tmp_path, exclusive=False):
        with pytest.raises(BlockingIOError):
            with maintenance_lock(tmp_path, exclusive=True):
                pass
    with maintenance_lock(tmp_path, exclusive=True):
        with pytest.raises(BlockingIOError):
            with maintenance_lock(tmp_path, exclusive=False):
                pass


def test_backup_commands_round_trip_and_verify_restored_files(tmp_path, monkeypatch):
    import spectarr.backup as backup

    database, storage, _, _ = snapshot_fixture(tmp_path)
    stdout = io.StringIO()
    stdout.buffer = io.BytesIO()
    monkeypatch.setattr(backup.sys, 'stdout', stdout)
    monkeypatch.setattr(backup.sys, 'argv', ['spectarr-backup', 'create-set', str(tmp_path)])
    assert backup.main() == 0
    stdin = io.StringIO()
    stdin.buffer = io.BytesIO(stdout.buffer.getvalue())
    monkeypatch.setattr(backup.sys, 'stdin', stdin)
    monkeypatch.setattr(backup.sys, 'argv', ['spectarr-backup', 'verify-set'])
    assert backup.main() == 0
    assert 'Verified 3 artifact objects' in stdout.getvalue()
    monkeypatch.setattr(backup.sys, 'argv', ['spectarr-backup', 'verify-files', str(database), str(storage.root)])
    assert backup.main() == 0
    stdout.buffer = io.BytesIO()
    monkeypatch.setattr(backup.sys, 'argv', ['spectarr-backup', 'create', str(database)])
    assert backup.main() == 0
    stdin.buffer = io.BytesIO(stdout.buffer.getvalue())
    monkeypatch.setattr(backup.sys, 'argv', ['spectarr-backup', 'verify'])
    assert backup.main() == 0
    assert 'SQLite backup is valid' in stdout.getvalue()


def test_backup_verifies_library_hardlinks_and_rejects_missing_targets(tmp_path):
    _, storage, source, _ = snapshot_fixture(tmp_path)
    storage.materialize(source.key, 'readable/source.raw', 'source.raw')
    output = io.BytesIO()
    create_backup_set(tmp_path, output)
    assert verify_backup_set(io.BytesIO(output.getvalue())) == 3
    broken = rewrite_archive(output.getvalue(), skip='storage/' + source.key)
    with pytest.raises(RuntimeError, match='Missing backup hard link target'):
        verify_backup_set(io.BytesIO(broken))


@pytest.mark.parametrize('kind', ['duplicate', 'symlink', 'cycle', 'no-database'])
def test_backup_rejects_ambiguous_or_incomplete_archives(kind):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as archive:
        member = tarfile.TarInfo('storage/example')
        if kind == 'symlink':
            member.type = tarfile.SYMTYPE
            member.linkname = '/etc/passwd'
        elif kind == 'cycle':
            member.type = tarfile.LNKTYPE
            member.linkname = 'storage/example'
        archive.addfile(member, io.BytesIO())
        if kind == 'duplicate':
            archive.addfile(member, io.BytesIO())
    with pytest.raises(RuntimeError):
        verify_backup_set(io.BytesIO(stream.getvalue()))
