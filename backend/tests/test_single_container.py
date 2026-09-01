from __future__ import annotations

import io
import json
import os
import sqlite3
import stat
import subprocess
from pathlib import Path
from unittest.mock import patch

from spectarr.backup import create_backup, verify_stream
from spectarr.database import make_engine
from spectarr.runtime import (
    _data_mount_source,
    prepare_docker_data_root,
    prepare_runtime_secrets,
    process_commands,
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


def test_data_mount_source_accepts_bind_mounts_and_named_volumes() -> None:
    assert _data_mount_source(
        [{"Type": "bind", "Source": "/srv/spectarr", "Destination": "/data"}]
    ) == Path("/srv/spectarr")
    assert _data_mount_source(
        [
            {
                "Type": "volume",
                "Source": "/var/lib/docker/volumes/spectarr-data/_data",
                "Destination": "/data",
            }
        ]
    ) == Path("/var/lib/docker/volumes/spectarr-data/_data")


def test_docker_data_root_is_discovered_from_the_running_container(monkeypatch) -> None:
    monkeypatch.delenv("SPECTARR_DOCKER_DATA_ROOT", raising=False)
    monkeypatch.setenv("SPECTARR_CONTAINER_ID", "container-id")
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='[{"Source":"/srv/spectarr","Destination":"/data"}]',
        stderr="",
    )
    with patch("spectarr.runtime.subprocess.run", return_value=completed) as inspect:
        assert prepare_docker_data_root() == Path("/srv/spectarr")
    inspect.assert_called_once_with(
        ["docker", "inspect", "container-id", "--format", "{{json .Mounts}}"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert os.environ["SPECTARR_DOCKER_DATA_ROOT"] == "/srv/spectarr"


def test_explicit_docker_data_root_skips_discovery(monkeypatch) -> None:
    monkeypatch.setenv("SPECTARR_DOCKER_DATA_ROOT", "/configured/data")
    with patch("spectarr.runtime.subprocess.run") as inspect:
        assert prepare_docker_data_root() == Path("/configured/data")
    inspect.assert_not_called()
