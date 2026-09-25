from __future__ import annotations

import logging
import shutil
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select

from .auth import is_read_request
from .config import get_settings
from .database import SessionLocal
from .models import Artifact, Job, JobState, UploadSession, UploadState
from .storage import LocalArtifactStorage
from .locking import file_lock, maintenance_lock
from .library_publication import LibraryPublication, publication_journal


logger = logging.getLogger(__name__)




def guard_storage_mutation(request: Request):
    settings = get_settings()
    path = request.scope["path"]
    library = (settings.library_root or settings.storage_root / "library").resolve()
    pending = publication_journal(library).exists()
    read_only = is_read_request(request) and "/upload-sessions" not in path
    if read_only and not pending:
        yield
        return
    if settings.restore_mode:
        raise HTTPException(503, "Restore verification mode is read-only", headers={"Retry-After": "60"})
    rebuild_request = (
        request.method == "POST" and path.endswith("/library/rebuild")
        or request.method == "PATCH" and path == f"{settings.api_prefix}/projects/{request.path_params.get('project_id')}"
    )
    exclusive = pending or rebuild_request
    job_path = f"{settings.api_prefix}/jobs/{request.path_params.get('job_id')}"
    starts_job = request.method == "POST" and path == f"{job_path}/claim" or request.method == "PATCH" and path == job_path
    try:
        with ExitStack() as locks:
            # Prevent a new lease between the idle check and publication, while
            # keeping the storage lock free for workers that are already active.
            if rebuild_request or starts_job:
                locks.enter_context(file_lock(
                    settings.storage_root / ".spectarr" / "job-start.lock",
                    exclusive=rebuild_request, blocking=starts_job,
                ))
            if rebuild_request:
                with SessionLocal() as probe:
                    if probe.scalar(select(Job.id).where(Job.state == JobState.RUNNING).limit(1)):
                        raise HTTPException(409, "Wait for running processing jobs to finish before updating a project or rebuilding the library")
            locks.enter_context(maintenance_lock(settings.storage_root, exclusive=exclusive))
            if not exclusive and publication_journal(library).exists():
                raise HTTPException(503, "Library recovery is pending. Retry shortly", headers={"Retry-After": "1"})
            if exclusive and publication_journal(library).exists():
                with SessionLocal() as recovery_session:
                    LibraryPublication(LocalArtifactStorage(settings.storage_root, library)).recover(recovery_session)
            if upload_id := request.path_params.get("upload_id"):
                with upload_lock(upload_id):
                    yield
            else:
                yield
    except BlockingIOError as error:
        raise HTTPException(503, "Storage maintenance is in progress", headers={"Retry-After": "10"}) from error


def cleanup_upload(storage: LocalArtifactStorage, upload: UploadSession) -> None:
    root = storage.staging / "uploads" / upload.id
    if root.exists():
        try:
            shutil.rmtree(root)
        except OSError:
            logger.exception("Could not remove completed or abandoned upload staging %s", upload.id)


@contextmanager
def upload_lock(upload_id: str):
    # IDs come from database records or validated UUID path values.
    from uuid import UUID

    try:
        normalized = str(UUID(upload_id))
    except ValueError as error:
        raise HTTPException(404, "Upload session not found") from error
    try:
        with file_lock(get_settings().storage_root / ".spectarr" / "upload-locks" / normalized, exclusive=True):
            yield
    except BlockingIOError as error:
        raise HTTPException(409, "Upload is busy. Retry shortly", headers={"Retry-After": "1"}) from error


def sweep_storage() -> dict[str, int]:
    """Reconcile expired staging and old unreferenced objects between API writes."""
    settings = get_settings()
    if settings.restore_mode:
        return {"uploads": 0, "objects": 0}
    removed = {"uploads": 0, "objects": 0}
    try:
        with maintenance_lock(settings.storage_root, exclusive=True), SessionLocal() as session:
            storage = LocalArtifactStorage(settings.storage_root, settings.library_root)
            now = datetime.now(timezone.utc)
            for upload in session.scalars(select(UploadSession)):
                if upload.state == UploadState.OPEN and upload.expires_at.replace(tzinfo=timezone.utc) <= now:
                    upload.state = UploadState.EXPIRED
                if upload.state in {UploadState.COMPLETED, UploadState.EXPIRED, UploadState.FAILED}:
                    cleanup_upload(storage, upload)
                    removed["uploads"] += 1
            session.commit()
            referenced = set(session.scalars(select(Artifact.storage_key)))
            cutoff = (now - timedelta(days=1)).timestamp()
            for base in (storage.objects, storage.bundles):
                for path in base.glob("*/*/*"):
                    key = path.relative_to(storage.root).as_posix()
                    if key not in referenced and path.stat().st_mtime < cutoff:
                        storage.remove_object(key)
                        removed["objects"] += 1
    except BlockingIOError:
        pass
    return removed
