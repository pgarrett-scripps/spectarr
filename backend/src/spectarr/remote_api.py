from __future__ import annotations

import shutil
from datetime import timezone
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.exc import IntegrityError

from . import pride, remote_sdrf
from .api import SessionDep, fetch_or_404
from .auth import require_admin, require_project_access, visibility
from .config import get_settings
from .models import Experiment, Project, RemoteDownloadSettings, RemoteImport
from .remote_settings import MAX_CONCURRENCY, download_settings

router = APIRouter(tags=["online imports"])


class DownloadPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    concurrency: int | None = Field(ge=1, le=MAX_CONCURRENCY, strict=True)


@router.get("/settings/downloads", dependencies=[Depends(require_admin)])
def get_download_settings():
    return download_settings()


@router.put("/settings/downloads", dependencies=[Depends(require_admin)])
def update_download_settings(payload: DownloadPreference, session: SessionDep):
    if payload.concurrency is None:
        if saved := session.get(RemoteDownloadSettings, 1):
            session.delete(saved)
    else:
        session.execute(insert(RemoteDownloadSettings).values(id=1, concurrency=payload.concurrency)
                        .on_conflict_do_update(index_elements=["id"], set_={"concurrency": payload.concurrency}))
    session.commit()
    return download_settings()


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_id: str = Field(min_length=1, max_length=255)
    run_name: str = Field(min_length=1, max_length=255)
    sample_name: str = Field(min_length=1, max_length=255)

    @field_validator("run_name", "sample_name")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("Name cannot be blank")
        return value.strip()


class Enqueue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accession: str = Field(max_length=2048)
    experiment_id: str
    files: list[Selection] = Field(min_length=1, max_length=500)
    sdrf_file_id: str | None = Field(default=None, max_length=255)
    sdrf_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def view(item: RemoteImport):
    keys = ("id", "batch_id", "project_id", "experiment_id", "accession", "file_id", "filename",
            "run_name", "sample_name", "state", "byte_size", "bytes_received", "attempts",
            "cancel_requested", "error", "run_id", "artifact_id", "created_at")
    result = {key: getattr(item, key) for key in keys}
    result["sdrf_rows"] = len((item.source.get("sdrf") or {}).get("rows", []))
    result["created_at"] = item.created_at.replace(tzinfo=timezone.utc).isoformat()
    return result


def resolve(value: str):
    try:
        return pride.lookup(value)
    except httpx.HTTPStatusError as error:
        message = "PRIDE dataset was not found or is not public" if error.response.status_code == 404 else "PRIDE is unavailable. Try again later"
        raise HTTPException(502, message) from error
    except (httpx.HTTPError, OSError) as error:
        raise HTTPException(502, "Could not reach PRIDE. Try again later") from error
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise HTTPException(422, str(error) if isinstance(error, ValueError) else "Unexpected PRIDE response") from error


@router.get("/repositories/pride")
def lookup_dataset(request: Request, session: SessionDep, accession: str = Query(max_length=2048), project_id: str | None = None):
    if project_id:
        fetch_or_404(session, Project, project_id)
        require_project_access(session, request.state.principal, project_id)
    result = resolve(accession)
    settings = get_settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    result["free_bytes"] = shutil.disk_usage(settings.storage_root).free
    result["max_file_bytes"] = settings.max_upload_bytes
    result["download_concurrency"] = download_settings()["concurrency"]
    imported = set(session.scalars(select(RemoteImport.file_id).where(
        RemoteImport.project_id == project_id, RemoteImport.accession == result["accession"],
        RemoteImport.state == "succeeded", RemoteImport.artifact_id.is_not(None),
    ))) if project_id else set()
    for file in result["files"]:
        file.pop("url", None)
        file["previously_imported"] = file["id"] in imported
        if file["byte_size"] > settings.max_upload_bytes:
            file.update(supported=False, reason="File exceeds the configured import size limit")
    return result


def resolve_sdrf(dataset, file_id):
    try:
        return remote_sdrf.preview(dataset, file_id)
    except (httpx.HTTPError, OSError, ValueError) as error:
        raise HTTPException(502, "Could not retrieve PRIDE SDRF metadata. Retry or import without SDRF") from error


@router.get("/repositories/pride/sdrf")
def preview_sdrf(accession: str = Query(max_length=2048), file_id: str = Query(max_length=255)):
    return resolve_sdrf(resolve(accession), file_id)


@router.post("/remote-imports", status_code=202)
def enqueue(request: Request, payload: Enqueue, session: SessionDep, idempotency_key: Annotated[UUID, Header()]):
    if not get_settings().remote_imports_enabled:
        raise HTTPException(503, "Online imports are disabled on this server")
    experiment = fetch_or_404(session, Experiment, payload.experiment_id)
    require_project_access(session, request.state.principal, experiment.project_id, write=True)
    try:
        accession = pride.accession_from_input(payload.accession)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    batch_id = str(uuid5(NAMESPACE_URL, f"remote:{request.state.principal.actor_id}:{idempotency_key}"))
    if bool(payload.sdrf_file_id) != bool(payload.sdrf_sha256):
        raise HTTPException(422, "Preview the SDRF before selecting it for import")
    sdrf_selection = {"file_id": payload.sdrf_file_id, "sha256": payload.sdrf_sha256} if payload.sdrf_file_id else None
    selection = {item.file_id: item for item in payload.files}
    if len(selection) != len(payload.files):
        raise HTTPException(422, "Select each repository file only once")

    def existing_batch():
        existing = list(session.scalars(select(RemoteImport).where(RemoteImport.batch_id == batch_id)))
        if existing and (len(existing) != len(selection) or any(
            item.file_id not in selection or item.accession != accession or item.experiment_id != experiment.id
            or item.source.get("sdrf_selection") != sdrf_selection
            or item.run_name != selection[item.file_id].run_name or item.sample_name != selection[item.file_id].sample_name
            for item in existing
        )):
            raise HTTPException(409, "Import key belongs to a different selection or destination")
        return existing

    if existing := existing_batch():
        return [view(item) for item in existing]
    dataset = resolve(accession)
    sdrf = resolve_sdrf(dataset, payload.sdrf_file_id) if payload.sdrf_file_id else None
    if sdrf and sdrf["sha256"] != payload.sdrf_sha256:
        raise HTTPException(409, "The repository SDRF changed. Refresh its preview before importing")
    files = {file["id"]: file for file in dataset.pop("files")}
    settings = get_settings()
    selected = []
    for key, choice in selection.items():
        source = files.get(key)
        if not source or not source["supported"] or source["byte_size"] > settings.max_upload_bytes:
            raise HTTPException(422, "Selection contains an unavailable, unsupported, or oversized file")
        selected.append((choice, source))
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    sizes = [source["byte_size"] for _, source in selected]
    required = sum(sizes) * (1 if settings.library_link_mode == "hardlink" else 2) + sum(sorted(sizes, reverse=True)[:download_settings()["concurrency"]])
    if shutil.disk_usage(settings.storage_root).free < required + 256 * 1024 * 1024:
        raise HTTPException(409, "Insufficient server disk space for the selection and staging")
    rows = []
    for choice, source in selected:
        row = RemoteImport(
            id=str(uuid5(UUID(batch_id), choice.file_id)), batch_id=batch_id,
            project_id=experiment.project_id, experiment_id=experiment.id,
            accession=accession, file_id=choice.file_id, filename=source["filename"],
            run_name=choice.run_name, sample_name=choice.sample_name,
            source={**source, "dataset": dataset, "sdrf_selection": sdrf_selection,
                    "sdrf": remote_sdrf.subset(sdrf, choice.file_id) if sdrf else None}, byte_size=source["byte_size"],
        )
        session.add(row)
        rows.append(row)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = existing_batch()
        if not existing:
            raise HTTPException(409, "Import destination changed. Refresh and try again") from None
        rows = existing
    return [view(item) for item in rows]


@router.get("/remote-imports")
def list_imports(request: Request, session: SessionDep, project_id: str | None = None,
                 offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)):
    query = select(RemoteImport).where(RemoteImport.project_id.in_(
        select(Project.id).where(visibility(request.state.principal, Project))
    ))
    if project_id:
        require_project_access(session, request.state.principal, project_id)
        query = query.where(RemoteImport.project_id == project_id)
    return [view(item) for item in session.scalars(query.order_by(RemoteImport.created_at.desc(), RemoteImport.id).offset(offset).limit(limit))]


def accessible(request, session, import_id):
    item = fetch_or_404(session, RemoteImport, import_id)
    require_project_access(session, request.state.principal, item.project_id, write=True)
    return item


@router.post("/remote-imports/{import_id}/cancel")
def cancel(request: Request, session: SessionDep, import_id: str):
    item = accessible(request, session, import_id)
    if item.state not in {"succeeded", "cancelled"}:
        item.cancel_requested = True
        if item.state in {"queued", "failed"}:
            item.state = "cancelled"
        session.commit()
    return view(item)


@router.post("/remote-imports/{import_id}/retry")
def retry(request: Request, session: SessionDep, import_id: str):
    item = accessible(request, session, import_id)
    if item.state not in {"failed", "cancelled"}:
        raise HTTPException(409, "Only failed or cancelled downloads can be retried")
    item.state = "queued"
    item.cancel_requested = False
    item.error = None
    item.attempts = 0
    item.retry_at = None
    session.commit()
    return view(item)
