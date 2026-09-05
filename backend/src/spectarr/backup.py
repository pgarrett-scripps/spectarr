from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from .locking import maintenance_lock


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
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchone()
    if result != ("ok",):
        raise RuntimeError(f"SQLite integrity check failed: {result}")
    if foreign_keys:
        raise RuntimeError(f"SQLite foreign key check failed: {foreign_keys}")


def required_objects(database: Path) -> dict[str, tuple[str, int | None]]:
    expected: dict[str, tuple[str, int | None]] = {}
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        rows = connection.execute(
            "SELECT storage_key, sha256, byte_size, bundle_manifest FROM artifacts WHERE state = 'ready'"
        )
        for key, digest, size, manifest in rows:
            root = safe_archive_name("storage/" + key)
            if isinstance(manifest, str):
                manifest = json.loads(manifest)
            if manifest:
                expected[root + "/manifest.json"] = (digest, None)
                for member in manifest["files"]:
                    path = safe_archive_name(root + "/payload/" + manifest["root_name"] + "/" + member["path"])
                    expected[path] = (member["sha256"], member["size"])
            else:
                expected[root] = (digest, size)
    return expected


def safe_archive_name(name: str) -> str:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in name:
        raise RuntimeError(f"Unsafe backup member: {name}")
    if path.parts[0] not in {"database.sqlite3", "storage", ".spectarr", "storage.tar"}:
        raise RuntimeError(f"Unexpected backup member: {name}")
    return path.as_posix()


def create_backup_set(data_root: Path, output, *, database_path: Path | None = None, storage_root: Path | None = None) -> None:
    """Keep object mutations quiescent until both snapshot components are archived."""
    storage_root = storage_root or data_root / "storage"
    with maintenance_lock(storage_root, exclusive=True, blocking=True):
        with tempfile.TemporaryDirectory(prefix="spectarr-backup-") as temporary:
            database = Path(temporary) / "database.sqlite3"
            with database.open("wb") as target:
                create_backup(database_path or data_root / "spectarr.db", target)
            verify_objects(database, storage_root)
            with tarfile.open(fileobj=output, mode="w|") as archive:
                archive.add(database, arcname="database.sqlite3")
                for name in ("storage", ".spectarr"):
                    path = storage_root if name == "storage" else data_root / name
                    if path.exists():
                        archive.add(path, arcname=name)


def verify_objects(database: Path, storage: Path) -> int:
    verify_database(database)
    expected = required_objects(database)
    for name, (digest, size) in expected.items():
        path = storage / PurePosixPath(name).relative_to("storage")
        if not path.resolve().is_relative_to(storage.resolve()) or not path.is_file():
            raise RuntimeError(f"Missing or unsafe artifact object: {name}")
        with path.open("rb") as source:
            actual, actual_size = digest_stream(source)
        if actual != digest or (size is not None and actual_size != size):
            raise RuntimeError(f"Artifact checksum or size mismatch: {name}")
    return len(expected)


def digest_stream(source) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := source.read(8 * 1024 * 1024):
        size += len(chunk)
        digest.update(chunk)
    return digest.hexdigest(), size


def verify_backup_set(stream) -> int:
    """Verify a streamed snapshot or an envelope containing the legacy pair."""
    with tempfile.TemporaryDirectory(prefix="spectarr-verify-") as temporary:
        database = Path(temporary) / "database.sqlite3"
        contents = {}
        links = {}
        names = set()

        def inspect_archive(source, nested=False):
            with tarfile.open(fileobj=source, mode="r|*") as archive:
                for member in archive:
                    name = safe_archive_name(member.name)
                    if name in names:
                        raise RuntimeError(f"Duplicate backup member: {name}")
                    names.add(name)
                    if member.isdir():
                        continue
                    if member.islnk():
                        links[name] = safe_archive_name(member.linkname)
                        continue
                    if not member.isfile():
                        raise RuntimeError(f"Unsupported backup member: {name}")
                    payload = archive.extractfile(member)
                    if payload is None:
                        raise RuntimeError(f"Unreadable backup member: {name}")
                    if name == "database.sqlite3" and not nested:
                        with database.open("wb") as destination:
                            shutil.copyfileobj(payload, destination)
                    elif name == "storage.tar" and not nested:
                        inspect_archive(payload, nested=True)
                    else:
                        contents[name] = digest_stream(payload)

        inspect_archive(stream)
        if not database.is_file():
            raise RuntimeError("Backup contains no database snapshot")
        verify_database(database)
        for name in links:
            target = name
            visited = set()
            while target in links:
                if target in visited:
                    raise RuntimeError(f"Cyclic backup hard link: {name}")
                visited.add(target)
                target = links[target]
            if target not in contents:
                raise RuntimeError(f"Missing backup hard link target: {name}")
            contents[name] = contents[target]
        expected = required_objects(database)
        for name, (digest, size) in expected.items():
            actual = contents.get(name)
            if actual is None or actual[0] != digest or (size is not None and actual[1] != size):
                raise RuntimeError(f"Missing or corrupt artifact object: {name}")
        return len(expected)


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
    create_set = subparsers.add_parser("create-set")
    create_set.add_argument("data_root", type=Path)
    subparsers.add_parser("verify-set")
    verify_files = subparsers.add_parser("verify-files")
    verify_files.add_argument("database", type=Path)
    verify_files.add_argument("storage", type=Path)
    args = parser.parse_args()
    if args.command == "create":
        create_backup(args.database, sys.stdout.buffer)
    elif args.command == "create-set":
        create_backup_set(args.data_root, sys.stdout.buffer)
    elif args.command == "verify-set":
        print(f"Verified {verify_backup_set(sys.stdin.buffer)} artifact objects")
    elif args.command == "verify-files":
        print(f"Verified {verify_objects(args.database, args.storage)} artifact objects")
    else:
        verify_stream(sys.stdin.buffer)
        print("SQLite backup is valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
