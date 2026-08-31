from __future__ import annotations

import io
import sqlite3

from spectarr.backup import create_backup, verify_stream
from spectarr.database import make_engine
from spectarr.runtime import process_commands


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
