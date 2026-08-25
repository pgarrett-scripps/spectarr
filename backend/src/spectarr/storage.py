from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


@dataclass(frozen=True)
class StoredObject:
    key: str
    sha256: str
    byte_size: int
    manifest: dict | None = None


class ArtifactStorage(Protocol):
    def ingest_stream(self, stream: BinaryIO) -> StoredObject: ...

    def ingest_path(self, source: Path) -> StoredObject: ...

    def resolve(self, key: str) -> Path: ...


class LocalArtifactStorage:
    """Immutable object storage with a separate human-readable library view."""

    def __init__(
        self,
        root: Path,
        library_root: Path | None = None,
        link_mode: str = "auto",
        project_template: str = "{project_name}__{project_id:8}",
        filename_template: str = "{run_name}__{sample_name}__{run_id:8}{extension}",
    ):
        self.root = root.resolve()
        self.internal = self.root / ".spectarr"
        self.objects = self.internal / "objects" / "sha256"
        self.bundles = self.internal / "bundles" / "sha256"
        self.staging = self.internal / "staging"
        self.library = (library_root or self.root / "library").resolve()
        if link_mode not in {"auto", "hardlink", "copy"}:
            raise ValueError("Library link mode must be auto, hardlink, or copy")
        self.link_mode = link_mode
        self.project_template = project_template
        self.filename_template = filename_template
        for directory in (self.objects, self.bundles, self.staging, self.library):
            directory.mkdir(parents=True, exist_ok=True)

    def ingest_stream(self, stream: BinaryIO) -> StoredObject:
        hasher = hashlib.sha256()
        byte_size = 0
        with tempfile.NamedTemporaryFile(dir=self.staging, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            while chunk := stream.read(8 * 1024 * 1024):
                hasher.update(chunk)
                byte_size += len(chunk)
                temporary.write(chunk)
            temporary.flush()
            os.fsync(temporary.fileno())
        digest = hasher.hexdigest()
        destination = self._object_path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            temporary_path.unlink()
        else:
            os.replace(temporary_path, destination)
            destination.chmod(0o444)
        return StoredObject(self._relative_key(destination), digest, byte_size)

    def ingest_path(self, source: Path) -> StoredObject:
        source = source.resolve(strict=True)
        if source.is_file():
            with source.open("rb") as handle:
                return self.ingest_stream(handle)
        if source.is_dir():
            return self._ingest_directory(source)
        raise ValueError("Source must be a regular file or directory")

    def resolve(self, key: str) -> Path:
        candidate = (self.root / key).resolve(strict=False)
        if not candidate.is_relative_to(self.root):
            raise ValueError("Storage key escapes the storage root")
        return candidate

    def resolve_library(self, key: str) -> Path:
        candidate = (self.library / key).resolve(strict=False)
        if not candidate.is_relative_to(self.library):
            raise ValueError("Library key escapes the library root")
        return candidate

    def materialize(self, storage_key: str, library_key: str, original_filename: str) -> str:
        source = self.resolve(storage_key)
        if not source.exists():
            raise FileNotFoundError(source)
        destination = self.resolve_library(library_key)
        if source.is_dir():
            payload = (source / "payload").resolve(strict=True)
            source = (payload / original_filename).resolve(strict=True)
            if not source.is_relative_to(payload):
                raise ValueError("Bundle filename escapes its immutable payload")
            if not source.is_dir():
                raise FileNotFoundError(source)
            return self._materialize_directory(source, destination)
        return self._materialize_file(source, destination)

    def write_library_json(self, key: str, payload: dict) -> Path:
        destination = self.resolve_library(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False).encode() + b"\n"
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(encoded)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, destination)
        destination.chmod(0o444)
        return destination

    def remove_library_key(self, key: str) -> None:
        destination = self.resolve_library(key)
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink(missing_ok=True)
        parent = destination.parent
        while parent != self.library and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def remove_object(self, key: str) -> bool:
        destination = self.resolve(key)
        allowed_roots = (self.objects, self.bundles)
        if not any(destination.is_relative_to(root) for root in allowed_roots):
            raise ValueError("Only immutable object keys can be removed")
        if not destination.exists():
            return False
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()
        parent = destination.parent
        boundary = next(root for root in allowed_roots if destination.is_relative_to(root))
        while parent != boundary and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return True

    def clear_library(self) -> None:
        if self.library.exists():
            shutil.rmtree(self.library)
        self.library.mkdir(parents=True, exist_ok=True)

    def check_writable(self) -> None:
        probe = self.staging / f"health-{os.getpid()}"
        probe.touch(exist_ok=False)
        probe.unlink()

    def _ingest_directory(self, source: Path) -> StoredObject:
        for candidate in sorted(source.rglob("*")):
            if candidate.is_symlink():
                raise ValueError(f"Directory bundles cannot contain symbolic links: {candidate}")
        temporary = Path(tempfile.mkdtemp(dir=self.staging))
        try:
            payload = temporary / "payload" / source.name
            shutil.copytree(source, payload, symlinks=False)
            files: list[dict[str, str | int]] = []
            total_bytes = 0
            for candidate in sorted(payload.rglob("*")):
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(payload).as_posix()
                file_digest, size = hash_file(candidate)
                total_bytes += size
                files.append({"path": relative, "size": size, "sha256": file_digest})
            if not files:
                raise ValueError("Directory bundle contains no files")
            manifest: dict[str, object] = {
                "version": 1,
                "root_name": source.name,
                "files": files,
                "byte_size": total_bytes,
            }
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            digest = hashlib.sha256(manifest_bytes).hexdigest()
            destination = self._bundle_path(digest)
            (temporary / "manifest.json").write_bytes(manifest_bytes)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                shutil.rmtree(temporary)
            else:
                try:
                    os.replace(temporary, destination)
                except OSError:
                    if not destination.exists():
                        raise
                for path in destination.rglob("*"):
                    if path.is_file():
                        path.chmod(0o444)
                    elif path.is_dir():
                        path.chmod(0o555)
                destination.chmod(0o555)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return StoredObject(self._relative_key(destination), digest, total_bytes, manifest)

    def _object_path(self, digest: str) -> Path:
        return self.objects / digest[:2] / digest[2:4] / digest

    def _bundle_path(self, digest: str) -> Path:
        return self.bundles / digest[:2] / digest[2:4] / digest

    def _relative_key(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _materialize_file(self, source: Path, destination: Path) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.is_file() and os.path.samestat(source.stat(), destination.stat()):
                return "hardlink"
            destination.unlink()
        temporary = destination.parent / f".{destination.name}.{os.getpid()}.tmp"
        temporary.unlink(missing_ok=True)
        mode = self._link_or_copy(source, temporary)
        os.replace(temporary, destination)
        destination.chmod(0o444)
        return mode

    def _materialize_directory(self, source: Path, destination: Path) -> str:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
        modes: set[str] = set()
        try:
            for candidate in sorted(source.rglob("*")):
                relative = candidate.relative_to(source)
                target = temporary / relative
                if candidate.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif candidate.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    modes.add(self._link_or_copy(candidate, target))
                else:
                    raise ValueError(f"Unsupported bundle entry: {candidate}")
            if destination.exists():
                shutil.rmtree(destination)
            os.replace(temporary, destination)
            for directory in [destination, *[path for path in destination.rglob("*") if path.is_dir()]]:
                directory.chmod(0o555)
            for file_path in (path for path in destination.rglob("*") if path.is_file()):
                file_path.chmod(0o444)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
        return "copy" if "copy" in modes else "hardlink"

    def _link_or_copy(self, source: Path, destination: Path) -> str:
        if self.link_mode != "copy":
            try:
                os.link(source, destination)
                return "hardlink"
            except OSError:
                if self.link_mode == "hardlink":
                    raise
        shutil.copyfile(source, destination)
        return "copy"


def hash_file(path: Path) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(8 * 1024 * 1024):
            hasher.update(chunk)
            size += len(chunk)
    return hasher.hexdigest(), size
