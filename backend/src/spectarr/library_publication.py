"""Recoverable publication of a staged library and its database transaction."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuditLog
from .storage import LocalArtifactStorage


def publication_journal(library: Path) -> Path:
    return library.parent / f".{library.name}.spectarr-rebuild.json"


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class LibraryPublication:
    """Caller holds the exclusive maintenance lock through recovery or commit.

    The audit row is the durable commit witness. A crash before the database
    commit restores the previous directory. A crash after it retains the new
    directory and completes cleanup. No database schema change is needed.
    """

    def __init__(self, storage: LocalArtifactStorage):
        self.storage = storage
        self.library = storage.library
        self.journal = publication_journal(self.library)

    def paths(self, token: str) -> tuple[Path, Path]:
        token = str(UUID(token))
        prefix = self.library.parent / f".{self.library.name}.rebuild-{token}"
        return Path(f"{prefix}.next"), Path(f"{prefix}.previous")

    def begin(self) -> tuple[str, Path]:
        if self.journal.exists():
            raise ValueError("A library publication requires recovery before rebuilding")
        if self.storage.internal.is_relative_to(self.library) or self.library.is_relative_to(self.storage.internal):
            raise ValueError("The readable library must be separate from internal object storage")
        if self.library.is_mount():
            raise ValueError("Library root is a mount point. Configure a directory inside that volume")
        token = str(uuid4())
        stage, _ = self.paths(token)
        temporary = self.journal.with_suffix(f".{token}.tmp")
        try:
            with temporary.open("x") as handle:
                json.dump({"token": token, "library": str(self.library)}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.journal)
            sync_directory(self.library.parent)
        finally:
            temporary.unlink(missing_ok=True)
        stage.mkdir()
        return token, stage

    def replace(self, source: Path, destination: Path) -> None:
        os.replace(source, destination)
        sync_directory(self.library.parent)

    def commit(self, session: Session, token: str, counts: dict[str, int]) -> None:
        stage, previous = self.paths(token)
        session.add(AuditLog(
            id=token, actor_type="system", action="library.rebuilt",
            resource_type="library", details={"root": str(self.library), **counts},
        ))
        session.flush()
        self.replace(self.library, previous)
        self.replace(stage, self.library)
        session.commit()

    def recover(self, session: Session) -> None:
        if not self.journal.exists():
            return
        record = json.loads(self.journal.read_text())
        if record.get("library") != str(self.library):
            raise ValueError("Library publication journal does not match this library root")
        token = str(UUID(record["token"]))
        stage, previous = self.paths(token)
        committed = session.scalar(select(AuditLog.id).where(
            AuditLog.id == token, AuditLog.action == "library.rebuilt",
        )) is not None
        if committed:
            marker = self.library / ".spectarr-generation"
            if not marker.is_file() or marker.read_text() != token:
                raise FileNotFoundError("Committed library generation is missing. Recovery requires inspection")
        elif previous.exists():
            if self.library.exists():
                LocalArtifactStorage._remove_tree(self.library)
            self.replace(previous, self.library)
        for path in (stage, previous):
            if path.exists():
                LocalArtifactStorage._remove_tree(path)
        sync_directory(self.library.parent)
        self.journal.unlink()
        sync_directory(self.library.parent)
