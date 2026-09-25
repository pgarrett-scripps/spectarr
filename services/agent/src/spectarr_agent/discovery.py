"""Read-only acquisition discovery, stability checks, and hashing."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .config import AgentConfig
from .readonly import open_source, tree_files


class AcquisitionChanged(RuntimeError):
    """Raised when an acquisition changes while it is being hashed."""


@dataclass(frozen=True)
class Candidate:
    path: Path
    kind: str
    format: str


@dataclass(frozen=True)
class Snapshot:
    signature: str
    byte_size: int
    file_count: int
    blocked: bool = False
    reason: str | None = None


@dataclass(frozen=True)
class HashedAcquisition:
    path: Path
    kind: str
    format: str
    checksum: str
    byte_size: int
    signature: str
    manifest: dict | None = None


class AcquisitionScanner:
    """Find supported acquisitions without following links or modifying sources."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self.errors: list[str] = []

    def discover(self) -> list[Candidate]:
        self.errors = []
        found: dict[str, Candidate] = {}
        for watch_path in self.config.watch_paths:
            if watch_path.is_symlink() or not watch_path.exists():
                self.errors.append(f"Root unavailable: {watch_path}")
                continue
            if self._is_candidate(watch_path):
                candidate = self._candidate(watch_path)
                found[os.path.normcase(str(candidate.path))] = candidate
                continue
            if not watch_path.is_dir():
                continue
            self._walk(watch_path, found)
        return [found[key] for key in sorted(found)]

    def _walk(self, root: Path, found: dict[str, Candidate]) -> None:
        try:
            entries = sorted(root.iterdir(), key=lambda item: item.name.casefold())
        except OSError as error:
            self.errors.append(f"Cannot traverse {root}: {error}")
            return
        for entry in entries:
            if entry.is_symlink() or self._ignored(entry.name):
                continue
            if self._is_candidate(entry):
                candidate = self._candidate(entry)
                found[os.path.normcase(str(candidate.path))] = candidate
            elif entry.is_dir():
                self._walk(entry, found)

    def _is_candidate(self, path: Path) -> bool:
        if self._ignored(path.name):
            return False
        suffix = path.suffix.lower()
        if path.is_dir():
            return suffix in self.config.bundle_suffixes
        return path.is_file() and suffix in self.config.file_suffixes

    def _candidate(self, path: Path) -> Candidate:
        suffix = path.suffix.lower()
        kind = "bundle" if path.is_dir() else "file"
        format_name = {
            ".mzml": "mzML",
            ".mzxml": "mzXML",
            ".mgf": "MGF",
            ".ms2": "MS2",
            ".wiff": "WIFF",
            ".wiff2": "WIFF2",
            ".raw": "RAW",
            ".d": "vendor_directory",
        }.get(suffix, suffix.removeprefix(".").upper() or "unknown")
        return Candidate(path.resolve(strict=True), kind, format_name)

    def _ignored(self, name: str) -> bool:
        lowered = name.casefold()
        return any(fnmatch.fnmatchcase(lowered, pattern.casefold()) for pattern in self.config.ignore_patterns)

    def snapshot(self, candidate: Candidate) -> Snapshot:
        if candidate.kind == "file":
            blocker = self._blocking_sibling(candidate.path)
            if blocker is not None:
                return Snapshot("blocked", 0, 0, True, f"Temporary marker present: {blocker.name}")
            stat = candidate.path.stat()
            signature = f"f:{stat.st_size}:{stat.st_mtime_ns}"
            return Snapshot(signature, stat.st_size, 1)
        records: list[tuple[str, int, int]] = []
        total = 0
        try:
            files = sorted(tree_files(candidate.path), key=lambda item: item.as_posix())
        except OSError as error:
            return Snapshot("blocked", 0, 0, True, str(error))
        for path in files:
            relative = path.relative_to(candidate.path).as_posix()
            if path.is_symlink():
                return Snapshot("blocked", total, len(records), True, f"Symbolic link present: {relative}")
            if self._ignored(path.name):
                return Snapshot("blocked", total, len(records), True, f"Temporary marker present: {relative}")
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError as error:
                return Snapshot("blocked", total, len(records), True, str(error))
            total += stat.st_size
            records.append((relative, stat.st_size, stat.st_mtime_ns))
        if not records:
            return Snapshot("blocked", 0, 0, True, "Bundle contains no files")
        encoded = json.dumps(records, separators=(",", ":"), ensure_ascii=True).encode()
        return Snapshot(hashlib.sha256(encoded).hexdigest(), total, len(records))

    def _blocking_sibling(self, path: Path) -> Path | None:
        name = path.name.casefold()
        stem = path.stem.casefold()
        try:
            siblings = path.parent.iterdir()
        except OSError:
            return None
        for sibling in siblings:
            lowered = sibling.name.casefold()
            if sibling == path or not self._ignored(sibling.name):
                continue
            normalized = lowered.lstrip("~")
            if normalized.startswith(f"{name}.") or normalized.startswith(f"{stem}."):
                return sibling
        return None

    def published(self, candidate: Candidate) -> bool:
        if self.config.completion_policy == "stability":
            return True
        marker = Path(str(candidate.path) + ".complete")
        return marker.is_file() and not marker.is_symlink()

    def hash_candidate(self, candidate: Candidate) -> HashedAcquisition:
        before = self.snapshot(candidate)
        if before.blocked or not self.published(candidate):
            raise AcquisitionChanged(before.reason or "Acquisition is blocked")
        if candidate.kind == "file":
            checksum, byte_size = hash_file(candidate.path)
            after = self.snapshot(candidate)
            if before.signature != after.signature or byte_size != after.byte_size or not self.published(candidate):
                raise AcquisitionChanged("File changed while being hashed")
            return HashedAcquisition(
                candidate.path,
                candidate.kind,
                candidate.format,
                checksum,
                byte_size,
                after.signature,
            )

        files: list[dict[str, str | int]] = []
        total = 0
        for path in sorted(tree_files(candidate.path), key=lambda item: item.as_posix()):
            if not path.is_file() or path.is_symlink():
                continue
            checksum, size = hash_file(path)
            total += size
            files.append(
                {
                    "path": path.relative_to(candidate.path).as_posix(),
                    "size": size,
                    "sha256": checksum,
                }
            )
        manifest: dict[str, object] = {
            "version": 1,
            "root_name": candidate.path.name,
            "files": files,
            "byte_size": total,
        }
        manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
        checksum = hashlib.sha256(manifest_bytes).hexdigest()
        after = self.snapshot(candidate)
        if before.signature != after.signature or total != after.byte_size or not self.published(candidate):
            raise AcquisitionChanged("Bundle changed while being hashed")
        return HashedAcquisition(
            candidate.path,
            candidate.kind,
            candidate.format,
            checksum,
            total,
            after.signature,
            manifest,
        )


def hash_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with open_source(path) as stream:
        before = os.fstat(stream.fileno())
        for chunk in iter(lambda: stream.read(min(8 * 1024 * 1024, before.st_size - size + 1)), b""):
            digest.update(chunk)
            size += len(chunk)
            if size > before.st_size:
                raise AcquisitionChanged("File grew while being hashed")
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise AcquisitionChanged("File changed while being hashed")
    return digest.hexdigest(), size


def manifest_files(acquisition: HashedAcquisition) -> Iterable[tuple[Path, dict[str, str | int]]]:
    """Yield verified bundle paths and their manifest records in canonical order."""

    if acquisition.manifest is None:
        return
    for raw in acquisition.manifest["files"]:
        record = dict(raw)
        relative = Path(str(record["path"]))
        path = (acquisition.path / relative).resolve(strict=True)
        if not path.is_relative_to(acquisition.path) or path.is_symlink():
            raise AcquisitionChanged("Bundle path escaped its acquisition root")
        yield path, record
