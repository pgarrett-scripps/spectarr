from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path


def create_backup(database: Path, output) -> None:
    if not database.is_file():
        raise FileNotFoundError(f"Spectarr database does not exist: {database}")
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as temporary:
        with sqlite3.connect(database) as source, sqlite3.connect(temporary.name) as destination:
            source.backup(destination)
        verify_database(Path(temporary.name))
        temporary.seek(0)
        shutil.copyfileobj(temporary, output)


def verify_database(database: Path) -> None:
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"SQLite integrity check failed: {result}")


def verify_stream(stream) -> None:
    with tempfile.NamedTemporaryFile(suffix=".sqlite3") as temporary:
        shutil.copyfileobj(stream, temporary)
        temporary.flush()
        verify_database(Path(temporary.name))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or verify a Spectarr SQLite backup")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("database", type=Path)
    subparsers.add_parser("verify")
    args = parser.parse_args()
    if args.command == "create":
        create_backup(args.database, sys.stdout.buffer)
    else:
        verify_stream(sys.stdin.buffer)
        print("SQLite backup is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
