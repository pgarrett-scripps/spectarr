"""External inventory. Recorded observations never imply managed ownership."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, timedelta
from pathlib import PurePosixPath
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select, func

from .api import SessionDep, fetch_or_404
from .auth import require_admin, require_agent, require_project_access
from .config import get_settings
from .locking import file_lock
from .models import (Agent, Experiment, ExternalEntry, ExternalLocation,
                     ExternalObservation, ExternalRevision, ExternalRoot, ExternalTask,
                     Project, UploadSession, utcnow)
from .schemas import BundleUploadManifest


def inventory_lock(request: Request):
    if request.method == "GET":
        yield
    else:
        with file_lock(get_settings().storage_root / ".spectarr" / "external.lock", exclusive=True, blocking=True):
            yield


router = APIRouter(tags=["external inventory"], dependencies=[Depends(inventory_lock)])


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RootCreate(Input):
    project_id: str
    agent_id: str
    label: str = Field(min_length=1, max_length=255)
    path: str = Field(min_length=1, max_length=2048)

    @field_validator("path")
    @classmethod
    def absolute_path(cls, value):
        if not value.startswith("/") or "\\" in value or "\x00" in value or ".." in value.split("/"):
            raise ValueError("Use an absolute Linux folder path. Windows roots are not yet qualified")
        return str(PurePosixPath(value))


class RootUpdate(Input):
    enabled: bool
    revalidate: bool = False


class Operation(Input):
    kind: Literal["scan", "verify", "import"]
    location_id: str | None = None
    revision_id: str | None = None
    experiment_id: str | None = None


class FileFact(Input):
    observed_at: datetime | None = None
    path: str = Field(min_length=1, max_length=2048)
    format: str = Field(min_length=1, max_length=32)
    kind: Literal["file", "bundle"]
    signature: str = Field(max_length=255)
    byte_size: int = Field(ge=0)
    readiness: Literal["unknown", "changing", "blocked", "stable_by_observation", "producer_published"] = "unknown"

    @field_validator("observed_at")
    @classmethod
    def observation_time(cls, value):
        if value is not None and (value.tzinfo is None or value > utcnow() + timedelta(minutes=5)):
            raise ValueError("Observation time must have a timezone and cannot be in the future")
        return value

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        if "\\" in value or "\x00" in value or any(p in {"", ".", ".."} for p in value.split("/")):
            raise ValueError("Expected a root-relative path without traversal")
        return value


class Report(Input):
    sequence: int = Field(ge=0)
    identity: str = Field(min_length=1, max_length=255)
    files: list[FileFact] = Field(default_factory=list, max_length=200)


class Result(Input):
    status: Literal["complete", "partial", "unavailable", "failed"]
    identity: str | None = Field(default=None, max_length=255)
    error: str | None = Field(default=None, max_length=2000)
    signature: str | None = Field(default=None, max_length=255)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    byte_size: int | None = Field(default=None, ge=0)
    manifest: BundleUploadManifest | None = None
    artifact_id: str | None = None
    readiness: Literal["stable_by_observation", "producer_published"] | None = None


class Relocation(Input):
    source_location_id: str
    candidate_location_id: str
    source_revision_id: str
    candidate_revision_id: str


def fields(item, names):
    return {name: getattr(item, name) for name in names.split()}


def allowed(session, request, project_id, write=False):
    if request.state.principal.agent_id:
        raise HTTPException(403, "Use the owning agent's task endpoints")
    require_project_access(session, request.state.principal, project_id, write=write)


def root_view(root):
    result = fields(root, "id project_id agent_id label path enabled status last_seen_at error")
    result["path_scope"] = "agent_host"
    result["freshness"] = freshness(root.last_seen_at)
    return result


def freshness(value):
    return "stale" if value is None or value.replace(tzinfo=timezone.utc) < utcnow() - timedelta(minutes=5) else "recent"


def root_for(session, request, root_id, write=False):
    root = fetch_or_404(session, ExternalRoot, root_id)
    allowed(session, request, root.project_id, write)
    return root


def owned(session, request, task_id, allow_disabled=False):
    principal = require_agent(request)
    task = fetch_or_404(session, ExternalTask, task_id)
    root = fetch_or_404(session, ExternalRoot, task.root_id)
    if root.agent_id != principal.agent_id or not root.enabled and not allow_disabled:
        raise HTTPException(403, "Agent does not own an enabled root")
    return task, root


def record(session, loc, task, **facts):
    session.add(ExternalObservation(location_id=loc.id, task_id=task.id if task else None, facts=facts))


def check_identity(root, identity):
    if not identity or root.identity and root.identity != identity:
        raise HTTPException(409, "Root identity changed. Revalidate the folder before scanning")
    root.identity = identity
    root.last_seen_at = utcnow()


@router.post("/external-roots", status_code=201)
def create_root(payload: RootCreate, request: Request, session: SessionDep):
    require_admin(request)
    fetch_or_404(session, Project, payload.project_id)
    fetch_or_404(session, Agent, payload.agent_id)
    for existing in session.scalars(select(ExternalRoot).where(ExternalRoot.agent_id == payload.agent_id)):
        a, b = PurePosixPath(existing.path), PurePosixPath(payload.path)
        if a.is_relative_to(b) or b.is_relative_to(a):
            raise HTTPException(409, "This agent already has an overlapping registered root")
    root = ExternalRoot(**payload.model_dump())
    session.add(root)
    session.commit()
    return root_view(root)


@router.get("/external-roots")
def list_roots(request: Request, session: SessionDep, project_id: str):
    allowed(session, request, project_id)
    return {"items": [root_view(r) for r in session.scalars(select(ExternalRoot).where(ExternalRoot.project_id == project_id))]}


@router.patch("/external-roots/{root_id}")
def update_root(root_id: str, payload: RootUpdate, request: Request, session: SessionDep):
    require_admin(request)
    root = root_for(session, request, root_id, True)
    root.enabled = payload.enabled
    if payload.revalidate:
        root.identity = None
        root.status = "unverified"
        root.last_seen_at = None
        for loc in session.scalars(select(ExternalLocation).where(ExternalLocation.root_id == root_id)):
            loc.status = "unverified"
    for task in session.scalars(select(ExternalTask).where(ExternalTask.root_id == root_id, ExternalTask.state == "pending")):
        task.state = "canceled"
    session.commit()
    return root_view(root)


@router.post("/external-roots/{root_id}/tasks", status_code=201)
def queue_task(root_id: str, payload: Operation, request: Request, session: SessionDep):
    root = root_for(session, request, root_id, True)
    if not root.enabled:
        raise HTTPException(409, "Folder tracking is paused")
    request_key = request.headers.get("Idempotency-Key")
    if request_key is not None:
        if not 1 <= len(request_key) <= 128:
            raise HTTPException(422, "Invalid request key")
        existing = session.scalar(select(ExternalTask).where(ExternalTask.root_id == root_id, ExternalTask.request_key == request_key))
        if existing:
            if any(getattr(existing, key) != value for key, value in payload.model_dump().items()):
                raise HTTPException(409, "Request key already belongs to a different operation")
            return fields(existing, "id kind state")
    if session.scalar(select(ExternalTask.id).where(ExternalTask.root_id == root_id, ExternalTask.state == "pending")):
        raise HTTPException(409, "This folder already has a pending operation")
    if payload.kind != "scan":
        loc = fetch_or_404(session, ExternalLocation, payload.location_id)
        if loc.root_id != root_id or loc.status != "observed":
            raise HTTPException(409, "Select an observed location in this root")
        if payload.kind == "import":
            revision = fetch_or_404(session, ExternalRevision, payload.revision_id)
            experiment = fetch_or_404(session, Experiment, payload.experiment_id)
            if loc.revision_id != revision.id or experiment.project_id != root.project_id:
                raise HTTPException(409, "Select the current revision and an experiment in this project")
    elif any([payload.location_id, payload.revision_id, payload.experiment_id]):
        raise HTTPException(422, "Scans cannot target an acquisition or experiment")
    task = ExternalTask(root_id=root_id, request_key=request_key, **payload.model_dump())
    session.add(task)
    session.commit()
    return fields(task, "id kind state")


@router.post("/external-tasks/{task_id}/cancel")
def cancel_task(task_id: str, request: Request, session: SessionDep):
    task = fetch_or_404(session, ExternalTask, task_id)
    root_for(session, request, task.root_id, True)
    if task.state == "pending":
        task.state = "canceled"
        session.commit()
    return fields(task, "id state")


@router.get("/external-roots/{root_id}/tasks")
def list_tasks(root_id: str, request: Request, session: SessionDep):
    root_for(session, request, root_id)
    return {"items": [fields(t, "id kind state error artifact_id created_at") for t in session.scalars(
        select(ExternalTask).where(ExternalTask.root_id == root_id).order_by(ExternalTask.created_at.desc()).limit(30))]}


@router.get("/external-agent/tasks")
def agent_tasks(request: Request, session: SessionDep):
    principal = require_agent(request)
    roots = list(session.scalars(select(ExternalRoot).where(ExternalRoot.agent_id == principal.agent_id, ExternalRoot.enabled.is_(True))))
    result = []
    for root in roots:
        for task in session.scalars(select(ExternalTask).where(ExternalTask.root_id == root.id, ExternalTask.state == "pending")):
            item = fields(task, "id kind sequence revision_id experiment_id")
            item["root"] = {**root_view(root), "identity": root.identity}
            if task.location_id:
                loc = session.get(ExternalLocation, task.location_id)
                entry = session.get(ExternalEntry, loc.entry_id)
                item["location"] = fields(loc, "id relative_path signature revision_id")
                item["entry"] = fields(entry, "id name format kind")
            if task.revision_id:
                item["revision"] = fields(session.get(ExternalRevision, task.revision_id), "id sha256 byte_size manifest")
            result.append(item)
    return {"items": result}


@router.post("/external-agent/tasks/{task_id}/observations")
def report_scan(task_id: str, payload: Report, request: Request, session: SessionDep):
    task, root = owned(session, request, task_id)
    if task.state != "pending" or task.kind != "scan":
        raise HTTPException(409, "Scan is no longer pending")
    check_identity(root, payload.identity)
    digest = hashlib.sha256(payload.model_dump_json().encode()).hexdigest()
    if payload.sequence == task.sequence - 1 and task.payload.get("batch_digest") == digest:
        return {"next_sequence": task.sequence}
    if payload.sequence != task.sequence:
        raise HTTPException(409, "Unexpected scan batch sequence")
    for fact in payload.files:
        loc = session.scalar(select(ExternalLocation).where(ExternalLocation.root_id == root.id, ExternalLocation.relative_path == fact.path))
        if loc is None:
            entry = ExternalEntry(project_id=root.project_id, name=PurePosixPath(fact.path).name, format=fact.format, kind=fact.kind)
            session.add(entry)
            session.flush()
            loc = ExternalLocation(root_id=root.id, entry_id=entry.id, relative_path=fact.path, signature=fact.signature, byte_size=fact.byte_size)
            session.add(loc)
            session.flush()
        entry = session.get(ExternalEntry, loc.entry_id)
        type_changed = (entry.kind, entry.format) != (fact.kind, fact.format)
        entry.kind = fact.kind
        entry.format = fact.format
        if type_changed or loc.signature != fact.signature or loc.status != "observed" or fact.readiness == "blocked":
            loc.revision_id = None
            loc.verified_at = None
        loc.signature = fact.signature
        loc.byte_size = fact.byte_size
        loc.readiness = fact.readiness
        loc.status = "observed"
        loc.observed_at = fact.observed_at or utcnow()
        loc.last_scan_id = task.id
        record(session, loc, task, **fact.model_dump(mode="json"))
    task.sequence += 1
    task.payload = {"batch_digest": digest}
    root.status = "scanning"
    root.error = None
    session.commit()
    return {"next_sequence": task.sequence}


def manifest_identity(manifest):
    data = manifest.model_dump()
    if len(data["files"]) > 10000:
        raise HTTPException(422, "Bundle exceeds the 10000 member verification limit")
    seen = set()
    for item in data["files"]:
        try:
            FileFact.safe_path(item["path"])
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if item["path"] in seen:
            raise HTTPException(422, "Duplicate bundle member")
        seen.add(item["path"])
        item["sha256"] = item["sha256"].lower()
    files = sorted(data["files"], key=lambda f: f["path"])
    full = {"version": 1, "root_name": data["root_name"], "files": files, "byte_size": sum(f["size"] for f in files)}
    digest = hashlib.sha256(json.dumps(full, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    comparison = {"version": "bundle-content-v1", "files": sorted(files, key=lambda f: f["path"])}
    fingerprint = hashlib.sha256(json.dumps(comparison, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return digest, "bundle-content-v1:" + fingerprint, full


@router.post("/external-agent/tasks/{task_id}/result")
def finish_task(task_id: str, payload: Result, request: Request, session: SessionDep):
    task, root = owned(session, request, task_id, allow_disabled=True)
    if not root.enabled and (task.kind != "import" or payload.status != "complete"):
        raise HTTPException(403, "Folder tracking is paused")
    if task.state == "canceled" and task.kind == "import" and payload.status == "complete":
        task.error = "Import completed before cancellation took effect"
    elif task.state != "pending":
        if task.state == payload.status:
            return {"id": task.id, "state": task.state}
        raise HTTPException(409, "Task is no longer pending")
    if payload.status == "complete":
        check_identity(root, payload.identity)
        if task.kind == "scan":
            if not task.sequence:
                raise HTTPException(409, "Report scan coverage before completion")
            for loc in session.scalars(select(ExternalLocation).where(ExternalLocation.root_id == root.id)):
                if loc.last_scan_id != task.id and loc.status != "not_found":
                    loc.status = "not_found"
                    record(session, loc, task, status="not_found")
            root.status = "available"
            root.error = None
        elif task.kind == "verify":
            loc = session.get(ExternalLocation, task.location_id)
            entry = session.get(ExternalEntry, loc.entry_id)
            if not payload.sha256 or payload.byte_size is None or not payload.signature or not payload.readiness:
                raise HTTPException(422, "Verification requires content and readiness evidence")
            if (entry.kind == "bundle") != bool(payload.manifest):
                raise HTTPException(422, "Verification kind does not match the acquisition")
            digest, fingerprint, manifest = (payload.sha256, "sha256:" + payload.sha256, None)
            if payload.manifest:
                digest, fingerprint, manifest = manifest_identity(payload.manifest)
                if manifest["root_name"] != PurePosixPath(loc.relative_path).name or manifest["byte_size"] != payload.byte_size:
                    raise HTTPException(422, "Bundle name or size does not match location")
            if digest != payload.sha256:
                raise HTTPException(422, "Manifest checksum mismatch")
            revision = session.scalar(select(ExternalRevision).where(ExternalRevision.entry_id == entry.id, ExternalRevision.sha256 == digest))
            if revision is None:
                revision = ExternalRevision(entry_id=entry.id, sha256=digest, fingerprint=fingerprint, manifest=manifest, byte_size=payload.byte_size)
                session.add(revision)
                session.flush()
            loc.revision_id = revision.id
            loc.signature = payload.signature
            loc.byte_size = payload.byte_size
            loc.verified_at = utcnow()
            loc.observed_at = utcnow()
            loc.status = "observed"
            loc.readiness = payload.readiness
            record(session, loc, task, revision_id=revision.id, sha256=digest)
        else:
            revision = session.get(ExternalRevision, task.revision_id)
            upload = session.scalar(select(UploadSession).where(UploadSession.agent_id == root.agent_id, UploadSession.idempotency_key == "external-import:" + task.id))
            if not upload or upload.state != "completed" or upload.artifact_id != payload.artifact_id or upload.expected_sha256 != revision.sha256:
                raise HTTPException(409, "No matching completed import")
            task.artifact_id = upload.artifact_id
            record(session, session.get(ExternalLocation, task.location_id), task, artifact_id=upload.artifact_id, revision_id=revision.id)
    else:
        root.error = payload.error
        if task.kind == "scan":
            root.status = "unavailable" if payload.status == "unavailable" else "partial"
        elif task.kind == "verify":
            loc = session.get(ExternalLocation, task.location_id)
            loc.revision_id = None
            loc.verified_at = None
            record(session, loc, task, error=payload.error, status=payload.status)
    task.state = payload.status
    task.error = payload.error or task.error
    session.commit()
    return {"id": task.id, "state": task.state}


@router.get("/external-entries")
def search_entries(request: Request, session: SessionDep, project_id: str, query: str = Query(default="", max_length=255), after: str = "", limit: int = Query(default=50, ge=1, le=100)):
    allowed(session, request, project_id)
    statement = select(ExternalEntry).where(ExternalEntry.project_id == project_id, ExternalEntry.alias_id.is_(None))
    if query:
        statement = statement.where(ExternalEntry.name.icontains(query, autoescape=True) | ExternalEntry.id.__eq__(query) | ExternalEntry.id.in_(
            select(ExternalLocation.entry_id).where(ExternalLocation.relative_path.icontains(query, autoescape=True))) | ExternalEntry.id.in_(
            select(ExternalRevision.entry_id).where(ExternalRevision.sha256 == query.removeprefix("sha256:"))))
    total = session.scalar(select(func.count()).select_from(statement.subquery()))
    rows = list(session.scalars(statement.where(ExternalEntry.id > after).order_by(ExternalEntry.id).limit(limit + 1)))
    return {"items": [entry_view(session, e) for e in rows[:limit]], "total": total, "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


def entry_view(session, entry):
    result = fields(entry, "id project_id name format kind alias_id")
    result["storage_mode"] = "external"
    result["locations"] = []
    for loc in session.scalars(select(ExternalLocation).where(ExternalLocation.entry_id == entry.id)):
        root = session.get(ExternalRoot, loc.root_id)
        agent = session.get(Agent, root.agent_id)
        value = fields(loc, "id root_id relative_path status readiness byte_size revision_id observed_at verified_at")
        value.update(root_label=root.label, root_path=root.path, root_status=root.status, enabled=root.enabled,
                     agent_id=root.agent_id, host=agent.metadata_json.get("hostname", agent.name), path_scope="agent_host",
                     path_flavor="posix", freshness=freshness(loc.observed_at), root_freshness=freshness(root.last_seen_at),
                     agent_freshness=freshness(agent.last_seen_at), mapping_required=True)
        if loc.revision_id:
            revision = session.get(ExternalRevision, loc.revision_id)
            value["sha256"] = revision.sha256
        result["locations"].append(value)
    return result


@router.get("/external-entries/{entry_id}/access")
def access_entry(entry_id: str, request: Request, session: SessionDep):
    entry = fetch_or_404(session, ExternalEntry, entry_id)
    allowed(session, request, entry.project_id)
    return {"schema_version": 1, **entry_view(session, entry), "integrity": "last_verification_not_current_guarantee",
            "usage_note": "Paths belong to the named agent host. Configure a root mapping and check local access before opening. External bytes are not backed up by MassSpec."}


@router.get("/external-entries/{entry_id}/history")
def entry_history(entry_id: str, request: Request, session: SessionDep, after: str = "", limit: int = Query(default=50, ge=1, le=100)):
    entry = fetch_or_404(session, ExternalEntry, entry_id)
    allowed(session, request, entry.project_id)
    ids = select(ExternalLocation.id).where(ExternalLocation.entry_id == entry.id)
    rows = list(session.scalars(select(ExternalObservation).where(ExternalObservation.location_id.in_(ids), ExternalObservation.id > after).order_by(ExternalObservation.id).limit(limit + 1)))
    return {"items": [fields(o, "id location_id task_id facts created_at") for o in rows[:limit]], "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.get("/external-entries/{entry_id}/matches")
def matches(entry_id: str, request: Request, session: SessionDep):
    entry = fetch_or_404(session, ExternalEntry, entry_id)
    allowed(session, request, entry.project_id)
    own = list(session.scalars(select(ExternalLocation).where(ExternalLocation.entry_id == entry_id)))
    fingerprints = [session.get(ExternalRevision, loc.revision_id).fingerprint for loc in own if loc.revision_id]
    candidates = session.scalars(select(ExternalEntry).where(ExternalEntry.project_id == entry.project_id, ExternalEntry.id != entry_id,
        ExternalEntry.alias_id.is_(None), ExternalEntry.id.in_(select(ExternalLocation.entry_id).where(
            ExternalLocation.status == "observed", ExternalLocation.revision_id.in_(select(ExternalRevision.id).where(ExternalRevision.fingerprint.in_(fingerprints)))))).limit(100))
    return {"items": [entry_view(session, e) for e in candidates], "meaning": "Matching content does not prove a shared acquisition occurrence"}


@router.post("/external-relocations")
def confirm_relocation(payload: Relocation, request: Request, session: SessionDep):
    old = fetch_or_404(session, ExternalLocation, payload.source_location_id)
    new = fetch_or_404(session, ExternalLocation, payload.candidate_location_id)
    root = root_for(session, request, old.root_id, True)
    other = root_for(session, request, new.root_id, True)
    if root.project_id != other.project_id or old.entry_id == new.entry_id or old.status != "not_found" or new.status != "observed":
        raise HTTPException(409, "Relocation requires a missing old location and an observed distinct candidate in the same project")
    a, b = session.get(ExternalRevision, old.revision_id), session.get(ExternalRevision, new.revision_id)
    if not a or not b or a.id != payload.source_revision_id or b.id != payload.candidate_revision_id or a.fingerprint != b.fingerprint:
        raise HTTPException(409, "Both locations require matching verified content and unchanged revisions")
    if session.scalar(select(ExternalTask.id).where(ExternalTask.root_id.in_([root.id, other.id]), ExternalTask.state == "pending")):
        raise HTTPException(409, "Wait for pending operations before confirming a relocation")
    candidate_entry = session.get(ExternalEntry, new.entry_id)
    candidate_entry.alias_id = old.entry_id
    for location in session.scalars(select(ExternalLocation).where(ExternalLocation.entry_id == candidate_entry.id)):
        location.entry_id = old.entry_id
    record(session, new, None, relocation_from=old.id, confirmed_candidate_entry_id=candidate_entry.id)
    session.commit()
    return {"entry_id": old.entry_id, "location_id": new.id}


def validate_external_upload(session, payload, agent_id, idempotency_key):
    task_id = payload.metadata_json.get("external_task_id")
    if task_id is None:
        if idempotency_key.startswith("external-import:"):
            raise HTTPException(422, "External import requires its task ID")
        return
    task = fetch_or_404(session, ExternalTask, task_id)
    root = session.get(ExternalRoot, task.root_id)
    loc = session.get(ExternalLocation, task.location_id) if task.location_id else None
    revision = session.get(ExternalRevision, task.revision_id) if task.revision_id else None
    if task.kind != "import" or task.state != "pending" or not root.enabled or root.agent_id != agent_id:
        raise HTTPException(403, "External import task does not belong to this agent")
    digest = manifest_identity(payload.bundle_manifest)[0] if payload.bundle_manifest else payload.sha256
    if (not revision or not loc or loc.revision_id != revision.id or digest != revision.sha256
            or payload.filename != PurePosixPath(loc.relative_path).name or payload.role != "source"
            or not payload.run or payload.run_id or payload.run.experiment_id != task.experiment_id
            or idempotency_key != "external-import:" + task.id):
        raise HTTPException(409, "Upload does not match the pinned external revision and destination")


def reset_restored_inventory(session):
    """A restored catalog must not resume requests against an external archive."""
    for root in session.scalars(select(ExternalRoot)):
        root.enabled = False
        root.status = "unverified"
        root.last_seen_at = None
    for task in session.scalars(select(ExternalTask).where(ExternalTask.state == "pending")):
        task.state = "canceled"
        task.error = "Canceled during restore. Revalidate the source root before requesting new work"
    session.commit()
