from __future__ import annotations

import logging
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request
from sqlalchemy import select

from .auth import is_read_request
from .config import get_settings
from .database import SessionLocal
from .models import Artifact, UploadSession, UploadState
from .storage import LocalArtifactStorage
from .locking import file_lock, maintenance_lock


logger = logging.getLogger(__name__)




def guard_storage_mutation(request: Request):
    if is_read_request(request) and "/upload-sessions" not in request.scope['path']:
        yield
        return
    settings = get_settings()
    if settings.restore_mode:
        raise HTTPException(503, "Restore verification mode is read-only", headers={"Retry-After": "60"})
    try:
        with maintenance_lock(settings.storage_root, exclusive=False):
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
