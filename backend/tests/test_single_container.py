from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from spectarr.backup import create_backup, verify_stream
from spectarr.database import make_engine
from spectarr.runtime import (
    _container_mount_map,
    _filesystem_type_for,
    prepare_docker_data_root,
    prepare_docker_mount_map,
    prepare_runtime_secrets,
    process_commands,
    verify_storage_identity,
)


def test_sqlite_engine_enables_durability_and_integrity_pragmas(tmp_path) -> None:
    database = tmp_path / "spectarr.db"
    engine = make_engine(f"sqlite:///{database}")
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            assert connection.exec_driver_sql("PRAGMA synchronous").scalar_one() == 2
            assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 10000
    finally:
        engine.dispose()


def test_online_backup_round_trip(tmp_path) -> None:
    database = tmp_path / "spectarr.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL)")
        connection.execute("INSERT INTO projects VALUES (?, ?)", ("project-1", "Test project"))

    output = io.BytesIO()
    create_backup(database, output)
    verify_stream(io.BytesIO(output.getvalue()))

    restored = tmp_path / "restored.db"
    restored.write_bytes(output.getvalue())
    with sqlite3.connect(restored) as connection:
        row = connection.execute("SELECT id, name FROM projects").fetchone()
    assert row == ("project-1", "Test project")


def test_supervisor_runs_every_component_in_one_container() -> None:
    commands = process_commands()
    assert [name for name, _command, _root in commands] == [
        "spectrum reader",
        "API",
        "converter",
        "extractor",
        "webhooks",
        "MCP",
    ]
    assert [name for name, _command, run_as_root in commands if run_as_root] == ["converter"]


def test_runtime_secrets_are_generated_and_reused(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("SPECTARR_SECRET_KEY", raising=False)
    monkeypatch.delenv("SPECTARR_WORKER_TOKEN", raising=False)

    secret_path = prepare_runtime_secrets(tmp_path)
    first = json.loads(secret_path.read_text(encoding="utf-8"))
    assert len(first["SPECTARR_SECRET_KEY"]) == 64
    assert len(first["SPECTARR_WORKER_TOKEN"]) == 64
    assert first["SPECTARR_SECRET_KEY"] != first["SPECTARR_WORKER_TOKEN"]
    assert stat.S_IMODE(secret_path.stat().st_mode) == 0o600

    monkeypatch.delenv("SPECTARR_SECRET_KEY")
    monkeypatch.delenv("SPECTARR_WORKER_TOKEN")
    prepare_runtime_secrets(tmp_path)
    assert json.loads(secret_path.read_text(encoding="utf-8")) == first
    assert os.environ["SPECTARR_SECRET_KEY"] == first["SPECTARR_SECRET_KEY"]
    assert os.environ["SPECTARR_WORKER_TOKEN"] == first["SPECTARR_WORKER_TOKEN"]


def test_runtime_secret_overrides_are_persisted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SPECTARR_SECRET_KEY", "s" * 64)
    monkeypatch.setenv("SPECTARR_WORKER_TOKEN", "w" * 64)

    secret_path = prepare_runtime_secrets(tmp_path)

    assert json.loads(secret_path.read_text(encoding="utf-8")) == {
        "SPECTARR_SECRET_KEY": "s" * 64,
        "SPECTARR_WORKER_TOKEN": "w" * 64,
    }


def test_container_mount_map_accepts_bind_mounts_and_named_volumes() -> None:
    assert _container_mount_map(
        [{"Type": "bind", "Source": "/srv/spectarr", "Destination": "/data"}]
    ) == {"/data": "/srv/spectarr"}
    assert _container_mount_map(
        [
            {
                "Type": "volume",
                "Source": "/var/lib/docker/volumes/spectarr-data/_data",
                "Destination": "/data",
            }
        ]
    ) == {"/data": "/var/lib/docker/volumes/spectarr-data/_data"}


def test_container_mount_map_keeps_every_mount() -> None:
    assert _container_mount_map(
        [
            {"Type": "bind", "Source": "/srv/spectarr", "Destination": "/data"},
            {"Type": "bind", "Source": "/tank/spectarr", "Destination": "/data/storage"},
            {"Type": "bind", "Source": "/run/docker.sock", "Destination": "/var/run/docker.sock"},
        ]
    ) == {
        "/data": "/srv/spectarr",
        "/data/storage": "/tank/spectarr",
        "/var/run/docker.sock": "/run/docker.sock",
    }


def test_docker_mount_map_is_discovered_from_the_running_container(monkeypatch) -> None:
    monkeypatch.delenv("SPECTARR_DOCKER_DATA_ROOT", raising=False)
    monkeypatch.delenv("SPECTARR_DOCKER_MOUNT_MAP", raising=False)
    monkeypatch.setenv("SPECTARR_CONTAINER_ID", "container-id")
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='[{"Source":"/srv/spectarr","Destination":"/data"},'
        '{"Source":"/tank/spectarr","Destination":"/data/storage"}]',
        stderr="",
    )
    with patch("spectarr.runtime.subprocess.run", return_value=completed) as inspect:
        assert prepare_docker_mount_map() == {
            "/data": "/srv/spectarr",
            "/data/storage": "/tank/spectarr",
        }
    inspect.assert_called_once_with(
        ["docker", "inspect", "container-id", "--format", "{{json .Mounts}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert os.environ["SPECTARR_DOCKER_DATA_ROOT"] == "/srv/spectarr"
    assert json.loads(os.environ["SPECTARR_DOCKER_MOUNT_MAP"]) == {
        "/data": "/srv/spectarr",
        "/data/storage": "/tank/spectarr",
    }


def test_docker_data_root_is_discovered_from_the_running_container(monkeypatch) -> None:
    monkeypatch.delenv("SPECTARR_DOCKER_DATA_ROOT", raising=False)
    monkeypatch.delenv("SPECTARR_DOCKER_MOUNT_MAP", raising=False)
    monkeypatch.setenv("SPECTARR_CONTAINER_ID", "container-id")
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='[{"Source":"/srv/spectarr","Destination":"/data"}]',
        stderr="",
    )
    with patch("spectarr.runtime.subprocess.run", return_value=completed):
        assert prepare_docker_data_root() == Path("/srv/spectarr")
    assert os.environ["SPECTARR_DOCKER_DATA_ROOT"] == "/srv/spectarr"


def test_explicit_docker_data_root_skips_discovery(monkeypatch) -> None:
    monkeypatch.delenv("SPECTARR_DOCKER_MOUNT_MAP", raising=False)
    monkeypatch.setenv("SPECTARR_DOCKER_DATA_ROOT", "/configured/data")
    with patch("spectarr.runtime.subprocess.run") as inspect:
        assert prepare_docker_data_root() == Path("/configured/data")
    inspect.assert_not_called()


def test_storage_identity_is_created_and_verified(tmp_path) -> None:
    identity = verify_storage_identity(tmp_path)
    assert (tmp_path / ".spectarr" / "storage-id").read_text().strip() == identity
    assert (tmp_path / "storage" / ".spectarr" / "storage-id").read_text().strip() == identity
    assert verify_storage_identity(tmp_path) == identity


def test_missing_storage_marker_refuses_to_start(tmp_path) -> None:
    verify_storage_identity(tmp_path)
    (tmp_path / "storage" / ".spectarr" / "storage-id").unlink()
    with pytest.raises(RuntimeError, match="probably missing or empty"):
        verify_storage_identity(tmp_path)


def test_foreign_storage_marker_refuses_to_start(tmp_path) -> None:
    verify_storage_identity(tmp_path)
    (tmp_path / "storage" / ".spectarr" / "storage-id").write_text("other-instance\n")
    with pytest.raises(RuntimeError, match="different Spectarr instance"):
        verify_storage_identity(tmp_path)


def test_existing_storage_is_adopted_by_a_fresh_data_directory(tmp_path) -> None:
    identity = verify_storage_identity(tmp_path)
    (tmp_path / ".spectarr" / "storage-id").unlink()
    assert verify_storage_identity(tmp_path) == identity
    assert (tmp_path / ".spectarr" / "storage-id").read_text().strip() == identity


def test_network_filesystem_detection_uses_the_deepest_mount() -> None:
    mounts = (
        "/dev/sda2 / ext4 rw 0 0\n"
        "//nas/data /data cifs rw 0 0\n"
        "tank/spectarr /data/storage zfs rw 0 0\n"
    )
    assert _filesystem_type_for(Path("/data/spectarr.db"), mounts) == "cifs"
    assert _filesystem_type_for(Path("/data/storage/objects/x"), mounts) == "zfs"
    assert _filesystem_type_for(Path("/home/user/spectarr.db"), mounts) == "ext4"
