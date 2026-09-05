from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, Field

from . import __version__
from .backup import create_backup_set, digest_stream, verify_backup_set, verify_objects
from .config import Settings, get_settings
from .locking import file_lock


logger = logging.getLogger(__name__)
SNAPSHOT_NAME = re.compile(r"backup-\d{8}T\d{6}Z-[0-9a-f]{32}")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class BackupPolicy(BaseModel):
    enabled: bool = False
    every_days: int = Field(default=1, ge=1, le=365)
    time_utc: str = Field(default="03:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    keep_last: int = Field(default=3, ge=1, le=1000)
    restore_every_days: int = Field(default=30, ge=1, le=365)


def next_backup(policy: BackupPolicy, now: datetime) -> str | None:
    if not policy.enabled:
        return None
    hour, minute = map(int, policy.time_utc.split(":"))
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=policy.every_days)
    return due.isoformat()


def restore_due(state: dict, policy: BackupPolicy, now: datetime) -> bool:
    return bool(state["last_restore_error"]) or not state["last_restore_at"] or now - datetime.fromisoformat(state["last_restore_at"]) >= timedelta(days=policy.restore_every_days)


def atomic_json(path: Path, payload: dict) -> None:
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, prefix=".state-", delete=False) as target:
        temporary = Path(target.name)
        try:
            json.dump(payload, target, indent=2)
            target.flush()
            os.fsync(target.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    sync_directory(path.parent)


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class BackupService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.database = Path(self.settings.database_url.removeprefix("sqlite:///"))
        self.data_root = self.database.resolve().parent
        self.control = self.data_root / ".spectarr"
        self.state_path = self.control / "backups.json"

    def _lock(self, name: str, *, blocking: bool = True):
        return file_lock(self.control / name, exclusive=True, blocking=blocking)

    def _load(self) -> dict:
        if self.state_path.exists():
            state = json.loads(self.state_path.read_text())
            BackupPolicy.model_validate(state["policy"])
            return state
        state = {
            "instance_id": str(uuid4()), "destination_id": None,
            "policy": BackupPolicy().model_dump(), "status": "idle", "operation": None,
            "next_backup_at": None, "last_attempt_at": None, "last_success_at": None,
            "last_restore_at": None, "last_error": None, "last_restore_error": None,
            "latest_backup": None, "active_directory": None,
        }
        atomic_json(self.state_path, state)
        return state

    def _update(self, **values) -> dict:
        with self._lock("backup-state.lock"):
            state = self._load()
            state.update(values)
            atomic_json(self.state_path, state)
            return state

    def _destination(self, state: dict, *, initialize: bool = False) -> Path:
        root = self.settings.backup_root
        if root is None:
            raise ValueError("No backup destination is configured. Mount a separate directory and set SPECTARR_BACKUP_ROOT.")
        root = root.resolve()
        if not root.is_dir():
            raise ValueError("Backup destination is unavailable. Check its mount before retrying.")
        for source in (self.data_root, self.settings.storage_root.resolve(), self.settings.library_root):
            if source is not None:
                source = source.resolve()
                if root.is_relative_to(source) or source.is_relative_to(root):
                    raise ValueError("Backup destination must be separate from the live data and storage directories.")
        marker = root / ".spectarr-backup-destination.json"
        if marker.is_symlink():
            raise ValueError("Backup destination marker cannot be a symbolic link.")
        if marker.exists():
            destination_id = json.loads(marker.read_text())["id"]
        elif initialize and state["destination_id"] is None:
            destination_id = str(uuid4())
            # Exclusive creation also protects shared destinations used by two instances.
            try:
                with marker.open("x") as target:
                    json.dump({"id": destination_id}, target)
                    target.flush()
                    os.fsync(target.fileno())
                sync_directory(root)
            except FileExistsError:
                destination_id = json.loads(marker.read_text())["id"]
        else:
            raise ValueError("Backup destination marker is missing. Restore the configured mount before retrying.")
        if state["destination_id"] not in {None, destination_id}:
            raise ValueError("A different backup destination is mounted. Restore the original destination and its marker.")
        if state["destination_id"] is None:
            if not initialize:
                return root
            state["destination_id"] = destination_id
            self._update(destination_id=destination_id)
        return root

    def _namespace(self, root: Path, state: dict) -> Path:
        namespace = root / f"spectarr-{state['instance_id']}"
        if namespace.is_symlink():
            raise ValueError("Backup namespace cannot be a symbolic link.")
        namespace.mkdir(mode=0o700, exist_ok=True)
        return namespace

    def _history(self, root: Path, state: dict) -> list[dict]:
        namespace = root / f"spectarr-{state['instance_id']}"
        if namespace.is_symlink():
            raise ValueError("Backup namespace cannot be a symbolic link.")
        history = []
        for path in namespace.glob("backup-*"):
            if not SNAPSHOT_NAME.fullmatch(path.name) or path.is_symlink() or not path.is_dir():
                continue
            try:
                metadata = json.loads((path / "backup.json").read_text())
                if metadata.get("instance_id") == state["instance_id"] and metadata.get("id") == path.name and metadata.get("verified_at"):
                    history.append(metadata)
            except (OSError, ValueError):
                logger.warning("Skipping an incomplete backup record: %s", path.name)
        return sorted(history, key=lambda item: item["created_at"], reverse=True)

    def status(self) -> dict:
        with self._lock("backup-state.lock"):
            state = self._load()
        available = False
        destination_error = None
        history = []
        free_bytes = None
        same_filesystem = False
        try:
            # Before initial configuration, report readiness without creating a marker.
            root = self.settings.backup_root
            if root and state["destination_id"] is None and not (root / ".spectarr-backup-destination.json").exists():
                if not root.is_dir():
                    raise ValueError("Backup destination is unavailable. Check its mount before retrying.")
                for source in (self.data_root, self.settings.storage_root.resolve(), self.settings.library_root):
                    if source and (root.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(root.resolve())):
                        raise ValueError("Backup destination must be separate from the live data and storage directories.")
            else:
                root = self._destination(state)
            if root is None:
                raise ValueError("No backup destination is configured.")
            available = True
            free_bytes = shutil.disk_usage(root).free
            same_filesystem = root.stat().st_dev in {self.data_root.stat().st_dev, self.settings.storage_root.stat().st_dev}
            history = self._history(root, state)
        except (OSError, ValueError, KeyError) as error:
            destination_error = str(error)
        return {
            **state, "configured": self.settings.backup_root is not None,
            "destination": str(self.settings.backup_root) if self.settings.backup_root else None,
            "destination_available": available, "destination_error": destination_error,
            "same_filesystem": same_filesystem, "free_bytes": free_bytes,
            "history": history[:50], "restore_mode": self.settings.restore_mode,
        }

    def configure(self, policy: BackupPolicy) -> dict:
        self._writable()
        with self._lock("backup-operation.lock", blocking=False):
            with self._lock("backup-state.lock"):
                state = self._load()
            if state["status"] == "queued":
                raise ValueError("Wait for the queued backup operation before changing its policy.")
            if policy.enabled:
                self._destination(state, initialize=True)
            self._update(policy=policy.model_dump(), next_backup_at=next_backup(policy, now_utc()))
        return self.status()

    def request(self, operation: str) -> dict:
        self._writable()
        with self._lock("backup-operation.lock", blocking=False):
            with self._lock("backup-state.lock"):
                state = self._load()
            if state["status"] == "queued":
                raise ValueError("A backup operation is already queued.")
            root = self._destination(state, initialize=True)
            if operation == "restore" and not self._history(root, state):
                raise ValueError("Create a verified backup before testing a restore.")
            self._update(status="queued", operation=operation, last_error=None)
        return self.status()

    def _writable(self) -> None:
        if self.settings.restore_mode:
            raise ValueError("Backup operations are disabled in restore verification mode.")
        if self.database.name == ":memory:":
            raise ValueError("Backups require an on-disk database.")

    def tick(self) -> None:
        if self.settings.restore_mode:
            return
        try:
            with self._lock("backup-operation.lock", blocking=False):
                self._tick_locked()
        except BlockingIOError:
            return

    def _tick_locked(self) -> None:
        with self._lock("backup-state.lock"):
            state = self._load()
        policy = BackupPolicy.model_validate(state["policy"])
        now = now_utc()
        if state.get("active_directory"):
            try:
                self._cleanup_interrupted(state)
            except (OSError, ValueError) as error:
                self._update(status="failed", last_error=f"Interrupted backup cleanup failed: {error}")
                return
        if state["status"] in {"creating", "verifying", "restoring"}:
            self._update(status="interrupted", last_error="The previous operation was interrupted. Completed backups were preserved.")
            return
        scheduled = policy.enabled and state["next_backup_at"] and now >= datetime.fromisoformat(state["next_backup_at"])
        if state["status"] != "queued" and not scheduled:
            return
        operation = state["operation"] if state["status"] == "queued" else "backup"
        self._update(status="creating" if operation == "backup" else "restoring", operation=operation, last_attempt_at=now.isoformat(), last_error=None)
        try:
            root = self._destination(state)
            namespace = self._namespace(root, state)
            if operation == "backup":
                latest = self._create(root, namespace, state, policy)
                self._update(last_success_at=latest["verified_at"], latest_backup=latest["id"])
                if restore_due(state, policy, now):
                    self._restore(namespace / latest["id"])
                self._retain(root, namespace, state, policy, latest["id"])
            else:
                history = self._history(root, state)
                if not history:
                    raise ValueError("No verified backup is available for a restore test.")
                self._restore(namespace / history[0]["id"])
            self._update(status="complete", last_error=None)
        except Exception as error:
            logger.exception("Backup operation failed")
            self._update(status="failed", last_error=str(error))
        finally:
            if scheduled and operation == "backup":
                self._update(next_backup_at=next_backup(policy, now_utc()))

    def _cleanup_interrupted(self, state: dict) -> None:
        name = state["active_directory"]
        if not (re.fullmatch(r"\.restore-[a-z0-9_]+", name) or (name.startswith(".partial-") and SNAPSHOT_NAME.fullmatch(name.removeprefix(".partial-")))):
            raise ValueError("Invalid interrupted backup directory record")
        root = self._destination(state)
        namespace = self._namespace(root, state)
        path = namespace / name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            for parent, directories, _ in os.walk(path):
                os.chmod(parent, 0o700)
                for directory in directories:
                    child = Path(parent) / directory
                    if not child.is_symlink():
                        os.chmod(child, 0o700)
            shutil.rmtree(path)
        self._update(active_directory=None)

    def _estimate_bytes(self) -> int:
        seen = set()
        size = self.database.stat().st_size
        for base in (self.settings.storage_root, self.control):
            for path in base.rglob("*"):
                if path.is_file():
                    stat = path.stat()
                    key = (stat.st_dev, stat.st_ino)
                    if key not in seen:
                        seen.add(key)
                        size += stat.st_size + 2048
        return size + 1024 * 1024

    def _space(self, root: Path, needed: int) -> None:
        if shutil.disk_usage(root).free < needed + self.settings.backup_min_free_bytes:
            raise ValueError("Insufficient free space at the backup destination. Existing backups were preserved.")

    def _create(self, root: Path, namespace: Path, state: dict, policy: BackupPolicy) -> dict:
        now = now_utc()
        self._space(root, self._estimate_bytes() * (2 if restore_due(state, policy, now) else 1))
        name = f"backup-{now.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex}"
        temporary = namespace / f".partial-{name}"
        temporary.mkdir(mode=0o700)
        self._update(active_directory=temporary.name)
        try:
            archive = temporary / "snapshot.tar"
            with archive.open("xb") as output:
                os.chmod(archive, 0o600)
                create_backup_set(self.data_root, output, database_path=self.database, storage_root=self.settings.storage_root)
                output.flush()
                os.fsync(output.fileno())
            self._update(status="verifying")
            with archive.open("rb") as source:
                count = verify_backup_set(source)
            with archive.open("rb") as source:
                checksum, byte_size = digest_stream(source)
            checksums = f"{checksum}  snapshot.tar\n"
            if self.settings.backup_image:
                image = temporary / "IMAGE"
                image.write_text(self.settings.backup_image + "\n")
                with image.open("rb") as source:
                    image_hash, _ = digest_stream(source)
                checksums += f"{image_hash}  IMAGE\n"
            metadata = {
                "id": name, "instance_id": state["instance_id"], "created_at": now.isoformat(),
                "verified_at": now_utc().isoformat(), "byte_size": byte_size,
                "artifact_objects": count, "sha256": checksum, "version": __version__,
            }
            atomic_json(temporary / "backup.json", metadata)
            (temporary / "SHA256SUMS").write_text(checksums)
            for path in temporary.iterdir():
                with path.open("rb") as source:
                    os.fsync(source.fileno())
            sync_directory(temporary)
            self._destination(state)
            temporary.rename(namespace / name)
            sync_directory(namespace)
            return metadata
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
            self._update(active_directory=None)

    def _restore(self, backup: Path) -> None:
        self._update(status="restoring", last_restore_error="Restore verification has not completed.")
        root = None
        try:
            self._space(backup.parent, (backup / "snapshot.tar").stat().st_size)
            with (backup / "snapshot.tar").open("rb") as source:
                digest, _ = digest_stream(source)
            metadata = json.loads((backup / "backup.json").read_text())
            if digest != metadata["sha256"]:
                raise ValueError("Backup archive checksum no longer matches its verified record.")
            with (backup / "snapshot.tar").open("rb") as source:
                verify_backup_set(source)
            with tempfile.TemporaryDirectory(prefix=".restore-", dir=backup.parent) as directory:
                root = Path(directory)
                self._update(active_directory=root.name)
                with tarfile.open(backup / "snapshot.tar") as archive:
                    archive.extractall(root, filter="data")
                database = root / "database.sqlite3"
                verify_objects(database, root / "storage")
                result = subprocess.run(
                    [sys.executable, "-m", "spectarr.backup_rehearsal", str(database), str(root / "storage")],
                    capture_output=True, text=True, timeout=self.settings.backup_restore_timeout_seconds,
                )
                if result.returncode:
                    raise RuntimeError("Restored API health check failed. " + result.stderr[-1500:])
            self._update(last_restore_at=now_utc().isoformat(), last_restore_error=None)
        except Exception as error:
            self._update(last_restore_error=str(error))
            raise
        finally:
            if root is None or not root.exists():
                self._update(active_directory=None)

    def _retain(self, root: Path, namespace: Path, state: dict, policy: BackupPolicy, latest: str) -> None:
        self._destination(state)
        history = self._history(root, state)
        keep = {latest}
        keep.update(entry["id"] for entry in [item for item in history if item["id"] != latest][:policy.keep_last - 1])
        for entry in history:
            if entry["id"] in keep:
                continue
            path = namespace / entry["id"]
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
        sync_directory(namespace)
