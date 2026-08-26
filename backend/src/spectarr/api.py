from __future__ import annotations

import hashlib
import json
import secrets
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, TypeVar
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import and_, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import __version__
from .auth import require_admin
from .config import Settings, get_settings
from .database import get_session
from .library import LibraryMaterializer
from .models import (
    Artifact,
    ArtifactRole,
    ArtifactState,
    AuditLog,
    ConversionRecipe,
    Experiment,
    Instrument,
    Job,
    JobKind,
    JobState,
    ProcessingBatch,
    Project,
    ProjectMembership,
    Run,
    RunAnnotation,
    RunSample,
    Sample,
    UserRole,
)
from .schemas import (
    AnnotationCreate,
    AnnotationRead,
    ArtifactRead,
    BulkRunAssignment,
    DerivativeRequest,
    DerivedPurgePreviewRequest,
    DerivedPurgeRequest,
    ExperimentCreate,
    ExperimentRead,
    ExperimentDeletionPreview,
    HealthRead,
    InstrumentCreate,
    InstrumentRead,
    JobCreate,
    JobRead,
    JobUpdate,
    PathImportRequest,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    RecipeCreate,
    RecipeRead,
    RecipeUpdate,
    ProcessingBatchPreview,
    ProcessingBatchRead,
    ProcessingBatchRequest,
    RunCreate,
    RunAssignmentUpdate,
    RunRead,
    SampleCreate,
    SampleRead,
    StorageReclaimRead,
    SdrfDocumentWrite,
    SdrfValidationRequest,
    SubmissionPreviewRead,
)
from .storage import LocalArtifactStorage, StoredObject
from .spectrum_reader import SpectrumReaderClient, SpectrumReaderError
from .pipeline import schedule_source_pipeline
from .processing import (
    batch_view,
    builtin_profile_for_format,
    cancel_processing_batch,
    create_processing_batch,
    ensure_builtin_profiles,
    preview_batch,
    reconcile_profile_rules,
    retry_processing_batch,
    recipe_snapshot,
)
from .lifecycle import (
    delete_experiment,
    experiment_deletion_preview,
    preview_derived_purge,
    purge_derived_artifacts,
)
from .sdrf import (
    COMMON_TEMPLATES,
    build_submission_zip,
    document_view,
    generate_document,
    parse_sdrf,
    replace_document,
    serialize_sdrf,
    submission_preview,
    validate_document,
)


router = APIRouter()
SessionDep = Annotated[Session, Depends(get_session)]
ModelT = TypeVar("ModelT")


async def settings_dependency() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(settings_dependency)]


async def get_storage(settings: SettingsDep) -> LocalArtifactStorage:
    return LocalArtifactStorage(
        settings.storage_root,
        settings.library_root,
        settings.library_link_mode,
        settings.library_project_template,
        settings.library_filename_template,
    )


StorageDep = Annotated[LocalArtifactStorage, Depends(get_storage)]


async def get_spectrum_reader(settings: SettingsDep) -> SpectrumReaderClient:
    return SpectrumReaderClient(
        settings.spectrum_reader_url,
        settings.worker_token,
        settings.spectrum_reader_timeout_seconds,
    )


SpectrumReaderDep = Annotated[SpectrumReaderClient, Depends(get_spectrum_reader)]


async def require_worker_token(
    settings: SettingsDep,
    request: Request,
    x_spectarr_worker_token: Annotated[str | None, Header()] = None,
) -> None:
    principal = getattr(request.state, "principal", None)
    if principal is not None and principal.allows("jobs:write"):
        return
    if settings.worker_token and x_spectarr_worker_token != settings.worker_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Valid worker token required")


WorkerAuthDep = Annotated[None, Depends(require_worker_token)]


def fetch_or_404(session: Session, model: type[ModelT], object_id: str) -> ModelT:
    instance = session.get(model, object_id)
    if instance is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{model.__name__} not found")
    return instance


def commit_or_conflict(session: Session, instance: ModelT) -> ModelT:
    try:
        session.add(instance)
        session.commit()
        session.refresh(instance)
        return instance
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A record with these values already exists") from error


@router.get("/system/health", response_model=HealthRead, tags=["system"])
async def system_health(session: SessionDep, storage: StorageDep) -> HealthRead:
    session.execute(text("SELECT 1"))
    storage.check_writable()
    return HealthRead(version=__version__)


@router.get("/storage", tags=["system"])
async def storage_locations(session: SessionDep, storage: StorageDep) -> list[dict]:
    usage = shutil.disk_usage(storage.root)
    ready = Artifact.state == ArtifactState.READY
    artifact_count = session.scalar(select(func.count(Artifact.id)).where(ready)) or 0
    logical_bytes = session.scalar(select(func.coalesce(func.sum(Artifact.byte_size), 0)).where(ready)) or 0
    return [
        {
            "id": "storage-primary",
            "name": "Primary library",
            "path": str(storage.library),
            "kind": "filesystem",
            "usedBytes": logical_bytes,
            "capacityBytes": usage.total,
            "status": "healthy",
            "artifactCount": artifact_count,
        }
    ]


@router.get("/library", tags=["library"])
async def library_status(session: SessionDep, storage: StorageDep) -> dict:
    materialized = session.scalar(
        select(func.count(Artifact.id)).where(Artifact.library_path.is_not(None))
    ) or 0
    total = session.scalar(
        select(func.count(Artifact.id)).where(Artifact.state == ArtifactState.READY)
    ) or 0
    return {
        "root": str(storage.library),
        "catalog": str(storage.library / "spectarr-library.json"),
        "layout": "project/format/tokenized-filename",
        "link_mode": storage.link_mode,
        "project_template": storage.project_template,
        "filename_template": storage.filename_template,
        "materialized_artifacts": materialized,
        "artifact_count": total,
        "healthy": materialized == total,
    }


@router.post("/library/rebuild", tags=["library"])
async def rebuild_library(session: SessionDep, storage: StorageDep) -> dict:
    try:
        result = LibraryMaterializer(storage).rebuild(session)
        session.commit()
    except (OSError, ValueError) as error:
        session.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Library rebuild failed: {error}") from error
    return {**result, "root": str(storage.library), "status": "rebuilt"}


@router.get("/runs/{run_id}/manifest", tags=["library"])
async def get_run_manifest(run_id: str, session: SessionDep, storage: StorageDep) -> dict:
    run = fetch_or_404(session, Run, run_id)
    key = LibraryMaterializer(storage).run_manifest_key(run)
    manifest = storage.resolve_library(key)
    if not manifest.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "Run manifest is missing. Rebuild the library view")
    return json.loads(manifest.read_text())


@router.get("/projects/{project_id}/manifest", tags=["library"])
async def get_project_manifest(project_id: str, session: SessionDep, storage: StorageDep) -> dict:
    project = fetch_or_404(session, Project, project_id)
    key = LibraryMaterializer(storage).project_manifest_key(project)
    manifest = storage.resolve_library(key)
    if not manifest.is_file():
        raise HTTPException(status.HTTP_409_CONFLICT, "Project manifest is missing. Rebuild the library view")
    return json.loads(manifest.read_text())


@router.get("/projects/{project_id}/library", tags=["library"])
async def get_project_library(project_id: str, session: SessionDep, storage: StorageDep) -> dict:
    project = fetch_or_404(session, Project, project_id)
    materializer = LibraryMaterializer(storage)
    project_directory = materializer.project_directory(project)
    artifacts = [
        artifact
        for experiment in project.experiments
        for run in experiment.runs
        for artifact in run.artifacts
        if artifact.library_path
    ]
    formats = sorted({materializer.format_directory(artifact) for artifact in artifacts})
    return {
        "project_id": project.id,
        "root": str(storage.resolve_library(project_directory.as_posix())),
        "relative_path": project_directory.as_posix(),
        "manifest": materializer.project_manifest_key(project),
        "formats": {
            format_name: {
                "path": str(storage.resolve_library((project_directory / format_name).as_posix())),
                "relative_path": (project_directory / format_name).as_posix(),
                "artifact_count": sum(
                    1 for artifact in artifacts if materializer.format_directory(artifact) == format_name
                ),
            }
            for format_name in formats
        },
    }


@router.get("/overview", tags=["system"])
async def overview(session: SessionDep, storage: StorageDep) -> dict:
    runs = list(session.scalars(select(Run).order_by(Run.created_at.desc()).limit(8)))
    jobs = list(session.scalars(select(Job).order_by(Job.created_at.desc()).limit(8)))
    projects = list(session.scalars(select(Project).order_by(Project.updated_at.desc()).limit(8)))
    locations = await storage_locations(session, storage)
    queue_depth = session.scalar(
        select(func.count(Job.id)).where(Job.state.in_([JobState.QUEUED, JobState.RUNNING]))
    ) or 0
    active_workers = session.scalar(
        select(func.count(func.distinct(Job.worker_id))).where(
            Job.state == JobState.RUNNING,
            Job.lease_expires_at > datetime.now(timezone.utc),
        )
    ) or 0
    format_counts = {
        str(format_name): count
        for format_name, count in session.execute(
            select(Artifact.format, func.count(Artifact.id)).group_by(Artifact.format)
        )
    }
    return {
        "runs": [run_view(run) for run in runs],
        "jobs": [job_view(job) for job in jobs],
        "projects": [project_view(project) for project in projects],
        "storage": locations,
        "health": {
            "api": "online",
            "workers": active_workers,
            "queueDepth": queue_depth,
            "version": __version__,
        },
        "stats": {
            "runs": session.scalar(select(func.count(Run.id))) or 0,
            "artifacts": session.scalar(select(func.count(Artifact.id))) or 0,
            "formatCounts": format_counts,
        },
    }


@router.post("/projects", response_model=ProjectRead, status_code=status.HTTP_201_CREATED, tags=["projects"])
async def create_project(payload: ProjectCreate, session: SessionDep, storage: StorageDep) -> Project:
    project = commit_or_conflict(session, Project(**payload.model_dump()))
    materializer = LibraryMaterializer(storage)
    materializer.write_project_manifest(project)
    materializer.write_catalog(session)
    return project


@router.get("/projects", tags=["projects"])
async def list_projects(
    request: Request, session: SessionDep, offset: int = 0, limit: int = Query(100, ge=1, le=500)
) -> list[dict]:
    query = select(Project).order_by(Project.name)
    principal = request.state.principal
    if principal.role == UserRole.VIEWER and principal.user_id:
        query = query.join(ProjectMembership).where(ProjectMembership.user_id == principal.user_id)
    projects = list(session.scalars(query.offset(offset).limit(limit)))
    return [project_view(project) for project in projects]


@router.get("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
async def get_project(project_id: str, session: SessionDep) -> Project:
    return fetch_or_404(session, Project, project_id)


@router.patch("/projects/{project_id}", response_model=ProjectRead, tags=["projects"])
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    session: SessionDep,
    storage: StorageDep,
) -> Project:
    project = fetch_or_404(session, Project, project_id)
    previous_name = project.name
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Project metadata conflicts with an existing project") from error
    session.refresh(project)
    materializer = LibraryMaterializer(storage)
    if project.name != previous_name:
        materializer.rebuild(session)
        session.commit()
    else:
        materializer.write_project_manifest(project)
        materializer.write_catalog(session)
    return project


@router.get("/sdrf/templates", tags=["sdrf"])
async def list_sdrf_templates() -> dict:
    return {"specification_version": "v1.1.0", "templates": COMMON_TEMPLATES}


@router.get("/projects/{project_id}/sdrf", tags=["sdrf"])
async def get_project_sdrf(project_id: str, session: SessionDep) -> dict:
    project = fetch_or_404(session, Project, project_id)
    if project.sdrf_document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This project does not have an SDRF document")
    return document_view(project.sdrf_document)


@router.put("/projects/{project_id}/sdrf", tags=["sdrf"])
async def put_project_sdrf(
    project_id: str,
    payload: SdrfDocumentWrite,
    session: SessionDep,
    storage: StorageDep,
) -> dict:
    project = fetch_or_404(session, Project, project_id)
    document = replace_document(
        session,
        project,
        [column.strip() for column in payload.columns],
        [[str(value) for value in row.values] for row in payload.rows],
        templates=payload.templates,
        source_filename=payload.source_filename,
        synchronize=payload.synchronize,
    )
    LibraryMaterializer(storage).write_project_manifest(project)
    return document_view(document)


@router.post("/projects/{project_id}/sdrf/import", tags=["sdrf"])
async def import_project_sdrf(
    project_id: str,
    session: SessionDep,
    storage: StorageDep,
    file: Annotated[UploadFile, File()],
    synchronize: Annotated[bool, Form()] = True,
) -> dict:
    project = fetch_or_404(session, Project, project_id)
    content = await file.read(50 * 1024 * 1024 + 1)
    columns, rows = parse_sdrf(content)
    document = replace_document(
        session,
        project,
        columns,
        rows,
        source_filename=Path(file.filename or f"{project.name}.sdrf.tsv").name,
        synchronize=synchronize,
    )
    LibraryMaterializer(storage).write_project_manifest(project)
    return document_view(document)


@router.post("/projects/{project_id}/sdrf/generate", tags=["sdrf"])
async def generate_project_sdrf(project_id: str, session: SessionDep, storage: StorageDep) -> dict:
    project = fetch_or_404(session, Project, project_id)
    document = generate_document(session, project)
    LibraryMaterializer(storage).write_project_manifest(project)
    return document_view(document)


@router.post("/projects/{project_id}/sdrf/validate", tags=["sdrf"])
async def validate_project_sdrf(
    project_id: str,
    payload: SdrfValidationRequest,
    session: SessionDep,
    storage: StorageDep,
) -> dict:
    project = fetch_or_404(session, Project, project_id)
    document = project.sdrf_document
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This project does not have an SDRF document")
    report = validate_document(document, ontology=payload.ontology)
    document.validation_report = report
    document.validation_engine = report["engine"]
    document.status = "valid" if report["valid"] else "invalid"
    session.commit()
    LibraryMaterializer(storage).write_project_manifest(project)
    return report


@router.get("/projects/{project_id}/sdrf/export", tags=["sdrf"])
async def export_project_sdrf(project_id: str, session: SessionDep) -> Response:
    project = fetch_or_404(session, Project, project_id)
    document = project.sdrf_document
    if document is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This project does not have an SDRF document")
    filename = Path(document.source_filename or f"{project.name}.sdrf.tsv").name
    return Response(
        serialize_sdrf(document.columns, [row.values for row in document.rows]),
        media_type="text/tab-separated-values; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/projects/{project_id}/submission/preview",
    response_model=SubmissionPreviewRead,
    tags=["sdrf"],
)
async def preview_submission(project_id: str, session: SessionDep) -> dict:
    return submission_preview(fetch_or_404(session, Project, project_id))


@router.get("/projects/{project_id}/submission/export", tags=["sdrf"])
async def export_submission(
    project_id: str,
    session: SessionDep,
    storage: StorageDep,
) -> StreamingResponse:
    project = fetch_or_404(session, Project, project_id)
    package = build_submission_zip(project, storage)
    async def content_stream():
        try:
            with package.open("rb") as handle:
                while chunk := handle.read(8 * 1024 * 1024):
                    yield chunk
        finally:
            package.unlink(missing_ok=True)

    encoded_filename = quote(f"{project.name}-repository-submission.zip")
    return StreamingResponse(
        content_stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment",
            "X-Spectarr-Filename": encoded_filename,
        },
    )


@router.post(
    "/experiments", response_model=ExperimentRead, status_code=status.HTTP_201_CREATED, tags=["experiments"]
)
async def create_experiment(
    payload: ExperimentCreate, session: SessionDep, storage: StorageDep
) -> Experiment:
    project = fetch_or_404(session, Project, payload.project_id)
    experiment = commit_or_conflict(session, Experiment(**payload.model_dump()))
    materializer = LibraryMaterializer(storage)
    materializer.write_project_manifest(project)
    materializer.write_catalog(session)
    return experiment


@router.get("/experiments", response_model=list[ExperimentRead], tags=["experiments"])
async def list_experiments(
    session: SessionDep,
    project_id: str | None = None,
    offset: int = 0,
    limit: int = Query(100, ge=1, le=500),
) -> list[Experiment]:
    query = select(Experiment).order_by(Experiment.created_at.desc())
    if project_id:
        query = query.where(Experiment.project_id == project_id)
    return list(session.scalars(query.offset(offset).limit(limit)))


@router.get("/experiments/{experiment_id}", response_model=ExperimentRead, tags=["experiments"])
async def get_experiment(experiment_id: str, session: SessionDep) -> Experiment:
    return fetch_or_404(session, Experiment, experiment_id)


@router.get(
    "/experiments/{experiment_id}/deletion-preview",
    response_model=ExperimentDeletionPreview,
    tags=["experiments"],
)
async def preview_experiment_deletion(
    experiment_id: str, request: Request, session: SessionDep
) -> dict:
    require_admin(request)
    experiment = fetch_or_404(session, Experiment, experiment_id)
    return experiment_deletion_preview(session, experiment)


@router.delete("/experiments/{experiment_id}", tags=["experiments"])
async def remove_experiment(
    experiment_id: str,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    confirmation: str = Query(min_length=1, max_length=255),
) -> dict:
    principal = require_admin(request)
    experiment = fetch_or_404(session, Experiment, experiment_id)
    return delete_experiment(
        session,
        storage,
        experiment,
        confirmation,
        principal.actor_type,
        principal.actor_id,
    )


@router.post(
    "/storage/reclaim/preview",
    response_model=StorageReclaimRead,
    tags=["storage"],
)
async def preview_storage_reclaim(
    payload: DerivedPurgePreviewRequest, session: SessionDep
) -> dict:
    return preview_derived_purge(session, payload)


@router.post(
    "/storage/reclaim",
    response_model=StorageReclaimRead,
    tags=["storage"],
)
async def reclaim_storage(
    payload: DerivedPurgeRequest,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
) -> dict:
    principal = request.state.principal
    return purge_derived_artifacts(
        session,
        storage,
        payload,
        principal.actor_type,
        principal.actor_id,
    )


@router.post("/samples", response_model=SampleRead, status_code=status.HTTP_201_CREATED, tags=["samples"])
async def create_sample(payload: SampleCreate, session: SessionDep) -> Sample:
    fetch_or_404(session, Experiment, payload.experiment_id)
    return commit_or_conflict(session, Sample(**payload.model_dump()))


@router.get("/samples", response_model=list[SampleRead], tags=["samples"])
async def list_samples(
    session: SessionDep,
    experiment_id: str | None = None,
    offset: int = 0,
    limit: int = Query(100, ge=1, le=500),
) -> list[Sample]:
    query = select(Sample).order_by(Sample.created_at.desc())
    if experiment_id:
        query = query.where(Sample.experiment_id == experiment_id)
    return list(session.scalars(query.offset(offset).limit(limit)))


@router.get("/samples/{sample_id}", response_model=SampleRead, tags=["samples"])
async def get_sample(sample_id: str, session: SessionDep) -> Sample:
    return fetch_or_404(session, Sample, sample_id)


@router.post(
    "/instruments", response_model=InstrumentRead, status_code=status.HTTP_201_CREATED, tags=["instruments"]
)
async def create_instrument(payload: InstrumentCreate, session: SessionDep) -> Instrument:
    return commit_or_conflict(session, Instrument(**payload.model_dump()))


@router.get("/instruments", response_model=list[InstrumentRead], tags=["instruments"])
async def list_instruments(session: SessionDep) -> list[Instrument]:
    return list(session.scalars(select(Instrument).order_by(Instrument.name)))


@router.get("/instruments/{instrument_id}", response_model=InstrumentRead, tags=["instruments"])
async def get_instrument(instrument_id: str, session: SessionDep) -> Instrument:
    return fetch_or_404(session, Instrument, instrument_id)


@router.post("/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED, tags=["runs"])
async def create_run(payload: RunCreate, session: SessionDep, storage: StorageDep) -> Run:
    experiment = fetch_or_404(session, Experiment, payload.experiment_id)
    if payload.sample_id:
        sample = fetch_or_404(session, Sample, payload.sample_id)
        if sample.experiment_id != payload.experiment_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Sample belongs to a different experiment")
    linked_samples: list[tuple[Sample, object]] = []
    for item in payload.samples:
        sample = fetch_or_404(session, Sample, item.sample_id)
        if sample.experiment.project_id != experiment.project_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Run samples must belong to the same project")
        linked_samples.append((sample, item))
    if payload.instrument_id:
        fetch_or_404(session, Instrument, payload.instrument_id)
    values = payload.model_dump(exclude={"samples"})
    if linked_samples and not values.get("sample_id"):
        values["sample_id"] = linked_samples[0][0].id
    run = Run(**values)
    session.add(run)
    session.flush()
    if payload.sample_id:
        session.add(RunSample(run=run, sample_id=payload.sample_id, position=0))
    else:
        for position, (sample, item) in enumerate(linked_samples):
            session.add(
                RunSample(
                    run=run,
                    sample=sample,
                    position=position,
                    label=item.label,
                    role=item.role,
                    metadata_json=item.metadata_json,
                )
            )
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Run or sample relationship already exists") from error
    session.refresh(run)
    materializer = LibraryMaterializer(storage)
    materializer.write_run_manifest(run)
    materializer.write_project_manifest(run.experiment.project)
    materializer.write_catalog(session)
    return run


@router.get("/runs", tags=["runs"])
async def list_runs(
    request: Request,
    session: SessionDep,
    experiment_id: str | None = None,
    sample_id: str | None = None,
    query: str | None = None,
    assignment_status: str | None = None,
    offset: int = 0,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    statement = select(Run).order_by(Run.created_at.desc())
    principal = request.state.principal
    if principal.role == UserRole.VIEWER and principal.user_id:
        statement = (
            statement.join(Experiment, Run.experiment_id == Experiment.id)
            .join(ProjectMembership, ProjectMembership.project_id == Experiment.project_id)
            .where(ProjectMembership.user_id == principal.user_id)
        )
    if experiment_id:
        statement = statement.where(Run.experiment_id == experiment_id)
    if sample_id:
        statement = statement.where(or_(Run.sample_id == sample_id, Run.sample_links.any(sample_id=sample_id)))
    if query:
        statement = statement.where(Run.name.ilike(f"%{query}%"))
    if assignment_status:
        statement = statement.where(Run.assignment_status == assignment_status)
    return [run_view(run) for run in session.scalars(statement.offset(offset).limit(limit))]


@router.get("/inbox", tags=["runs"])
async def list_inbox(request: Request, session: SessionDep) -> list[dict]:
    statement = select(Run).where(Run.assignment_status == "needs_assignment").order_by(Run.created_at.desc())
    principal = request.state.principal
    if principal.role == UserRole.VIEWER and principal.user_id:
        statement = (
            statement.join(Experiment, Run.experiment_id == Experiment.id)
            .join(ProjectMembership, ProjectMembership.project_id == Experiment.project_id)
            .where(ProjectMembership.user_id == principal.user_id)
        )
    return [run_view(run) for run in session.scalars(statement)]


@router.post("/runs/bulk-assignment", tags=["runs"])
async def bulk_assign_runs(
    payload: BulkRunAssignment,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
) -> list[dict]:
    if len(set(payload.run_ids)) != len(payload.run_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "run_ids must be unique")
    runs = list(session.scalars(select(Run).where(Run.id.in_(payload.run_ids))))
    if len(runs) != len(payload.run_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more runs were not found")
    assigned = assign_runs(session, storage, request, runs, payload.experiment_id, payload.sample_id)
    return [run_view(run) for run in assigned]


@router.patch("/runs/{run_id}/assignment", tags=["runs"])
async def assign_run(
    run_id: str,
    payload: RunAssignmentUpdate,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
) -> dict:
    run = fetch_or_404(session, Run, run_id)
    return run_view(assign_runs(session, storage, request, [run], payload.experiment_id, payload.sample_id)[0])


@router.get("/runs/{run_id}", tags=["runs"])
async def get_run(run_id: str, session: SessionDep) -> dict:
    return run_view(fetch_or_404(session, Run, run_id))


@router.post("/recipes", response_model=RecipeRead, status_code=status.HTTP_201_CREATED, tags=["processing"])
async def create_recipe(payload: RecipeCreate, session: SessionDep) -> ConversionRecipe:
    values = payload.model_dump()
    values["parameters"] = payload.parameters.model_dump(exclude_none=True)
    return commit_or_conflict(session, ConversionRecipe(**values))


@router.get("/recipes", response_model=list[RecipeRead], tags=["processing"])
async def list_recipes(session: SessionDep, enabled: bool | None = None) -> list[ConversionRecipe]:
    ensure_builtin_profiles(session)
    query = select(ConversionRecipe).order_by(ConversionRecipe.name)
    if enabled is not None:
        query = query.where(ConversionRecipe.enabled == enabled)
    return list(session.scalars(query))


@router.get("/recipes/{recipe_id}", response_model=RecipeRead, tags=["processing"])
async def get_recipe(recipe_id: str, session: SessionDep) -> ConversionRecipe:
    return fetch_or_404(session, ConversionRecipe, recipe_id)


@router.patch("/recipes/{recipe_id}", response_model=RecipeRead, tags=["processing"])
async def update_recipe(
    recipe_id: str,
    payload: RecipeUpdate,
    request: Request,
    session: SessionDep,
) -> ConversionRecipe:
    principal = request.state.principal
    if not principal.allows("jobs:write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Processing profile changes require jobs:write")
    recipe = fetch_or_404(session, ConversionRecipe, recipe_id)
    values = payload.model_dump(exclude_unset=True)
    if recipe.system and values.get("name") and values["name"] != recipe.name:
        raise HTTPException(status.HTTP_409_CONFLICT, "Built-in profile names cannot be changed")
    if "parameters" in values and payload.parameters is not None:
        values["parameters"] = payload.parameters.model_dump(exclude_none=True)
    next_parameters = values.get("parameters", recipe.parameters)
    next_format = values.get("output_format", recipe.output_format)
    if next_parameters.get("preset"):
        expected_format = "MGF" if next_parameters["preset"] == "casanovo_mgf" else "mzML"
        if next_format != expected_format:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{next_parameters['preset']} produces {expected_format}",
            )
    changed_definition = any(
        key in values and values[key] != getattr(recipe, key)
        for key in {"output_format", "parameters"}
    )
    for key, value in values.items():
        setattr(recipe, key, value)
    if changed_definition:
        recipe.revision += 1
    session.commit()
    session.refresh(recipe)
    if changed_definition or values.get("enabled") is True:
        reconcile_profile_rules(session, recipe.id)
    return recipe


@router.post(
    "/processing-batches/preview",
    response_model=ProcessingBatchPreview,
    tags=["processing"],
)
async def preview_processing_batch(
    payload: ProcessingBatchRequest,
    request: Request,
    session: SessionDep,
) -> dict:
    if not request.state.principal.allows("jobs:write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Batch processing requires jobs:write")
    return preview_batch(session, payload)


@router.post(
    "/processing-batches",
    response_model=ProcessingBatchRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["processing"],
)
async def queue_processing_batch(
    payload: ProcessingBatchRequest,
    request: Request,
    session: SessionDep,
) -> dict:
    principal = request.state.principal
    if not principal.allows("jobs:write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Batch processing requires jobs:write")
    batch = create_processing_batch(session, payload, principal.user_id or principal.actor_id)
    return batch_view(batch)


@router.get("/processing-batches", response_model=list[ProcessingBatchRead], tags=["processing"])
async def list_processing_batches(
    session: SessionDep,
    offset: int = 0,
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    batches = session.scalars(
        select(ProcessingBatch).order_by(ProcessingBatch.created_at.desc()).offset(offset).limit(limit)
    )
    return [batch_view(batch, include_items=False) for batch in batches]


@router.get(
    "/processing-batches/{batch_id}",
    response_model=ProcessingBatchRead,
    tags=["processing"],
)
async def get_processing_batch(batch_id: str, session: SessionDep) -> dict:
    return batch_view(fetch_or_404(session, ProcessingBatch, batch_id))


@router.post(
    "/processing-batches/{batch_id}/retry",
    response_model=ProcessingBatchRead,
    tags=["processing"],
)
async def retry_batch(batch_id: str, request: Request, session: SessionDep) -> dict:
    if not request.state.principal.allows("jobs:write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Batch retry requires jobs:write")
    batch = retry_processing_batch(session, fetch_or_404(session, ProcessingBatch, batch_id))
    return batch_view(batch)


@router.post(
    "/processing-batches/{batch_id}/cancel",
    response_model=ProcessingBatchRead,
    tags=["processing"],
)
async def cancel_batch(batch_id: str, request: Request, session: SessionDep) -> dict:
    if not request.state.principal.allows("jobs:write"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Batch cancellation requires jobs:write")
    batch = cancel_processing_batch(session, fetch_or_404(session, ProcessingBatch, batch_id))
    return batch_view(batch)


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactRead], tags=["artifacts"])
async def list_run_artifacts(run_id: str, session: SessionDep) -> list[Artifact]:
    fetch_or_404(session, Run, run_id)
    return list(session.scalars(select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at)))


@router.get("/artifacts", response_model=list[ArtifactRead], tags=["artifacts"])
async def list_artifacts(
    session: SessionDep,
    run_id: str | None = None,
    role: ArtifactRole | None = None,
    format: str | None = None,
    updated_after: datetime | None = None,
    offset: int = 0,
    limit: int = Query(100, ge=1, le=500),
) -> list[Artifact]:
    query = select(Artifact).order_by(Artifact.updated_at, Artifact.id)
    if run_id:
        query = query.where(Artifact.run_id == run_id)
    if role:
        query = query.where(Artifact.role == role)
    if format:
        query = query.where(func.lower(Artifact.format) == format.lower())
    if updated_after:
        query = query.where(Artifact.updated_at > updated_after)
    return list(session.scalars(query.offset(offset).limit(limit)))


@router.get("/artifacts/{artifact_id}", response_model=ArtifactRead, tags=["artifacts"])
async def get_artifact(artifact_id: str, session: SessionDep) -> Artifact:
    return fetch_or_404(session, Artifact, artifact_id)


@router.get("/artifacts/{artifact_id}/spectra", tags=["spectra"])
async def browse_artifact_spectra(
    artifact_id: str,
    session: SessionDep,
    storage: StorageDep,
    spectrum_reader: SpectrumReaderDep,
    ms_level: int = Query(1, ge=1, le=2),
    offset: int = Query(0, ge=0, le=10_000_000),
    limit: int = Query(25, ge=1, le=100),
    rt_seconds: float | None = Query(None, ge=0),
    scan_number: int | None = Query(None, ge=0),
    native_id: str | None = Query(None, min_length=1, max_length=2048),
    precursor_mz: float | None = Query(None, ge=0),
) -> dict:
    artifact = fetch_or_404(session, Artifact, artifact_id)
    if artifact.state == ArtifactState.MISSING:
        raise HTTPException(
            status.HTTP_410_GONE,
            "Artifact content was purged and can be regenerated",
        )
    if not artifact.library_path:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Artifact has not been materialized in the library",
        )
    finders = [
        rt_seconds is not None,
        scan_number is not None,
        native_id is not None,
        precursor_mz is not None,
    ]
    if sum(finders) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Choose only one spectrum catalog search",
        )
    source = storage.resolve_library(artifact.library_path)
    if not source.exists():
        raise HTTPException(
            status.HTTP_410_GONE, "Artifact library content is missing"
        )
    if not source.is_relative_to(storage.root):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Spectrum reader requires the library to be located inside the shared storage root",
        )
    payload = {
        "relative_path": source.relative_to(storage.root).as_posix(),
        "ms_level": ms_level,
        "offset": offset,
        "limit": limit,
        "rt_seconds": rt_seconds,
        "scan_number": scan_number,
        "native_id": native_id,
        "precursor_mz": precursor_mz,
    }
    try:
        return await spectrum_reader.catalog(payload)
    except SpectrumReaderError as error:
        mapped_status = (
            error.status
            if error.status in {400, 403, 404, 409, 422, 502, 503, 504}
            else 502
        )
        raise HTTPException(mapped_status, str(error)) from error


@router.get("/artifacts/{artifact_id}/spectrum", tags=["spectra"])
async def get_artifact_spectrum(
    artifact_id: str,
    session: SessionDep,
    storage: StorageDep,
    spectrum_reader: SpectrumReaderDep,
    ms_level: int = Query(1, ge=1, le=2),
    index: int | None = Query(None, ge=0, le=10_000_000),
    scan_number: int | None = Query(None, ge=0),
    native_id: str | None = Query(None, min_length=1, max_length=2048),
) -> dict:
    artifact = fetch_or_404(session, Artifact, artifact_id)
    if artifact.state == ArtifactState.MISSING:
        raise HTTPException(status.HTTP_410_GONE, "Artifact content was purged and can be regenerated")
    if not artifact.library_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "Artifact has not been materialized in the library")
    selectors = [index is not None, scan_number is not None, native_id is not None]
    if sum(selectors) > 1:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Choose only one of index, scan_number, or native_id",
        )
    if not any(selectors):
        index = 0
    source = storage.resolve_library(artifact.library_path)
    if not source.exists():
        raise HTTPException(status.HTTP_410_GONE, "Artifact library content is missing")
    if not source.is_relative_to(storage.root):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Spectrum reader requires the library to be located inside the shared storage root",
        )
    payload = {
        "relative_path": source.relative_to(storage.root).as_posix(),
        "ms_level": ms_level,
        "index": index,
        "scan_number": scan_number,
        "native_id": native_id,
    }
    try:
        return await spectrum_reader.read(payload)
    except SpectrumReaderError as error:
        mapped_status = error.status if error.status in {400, 403, 404, 409, 422, 502, 503, 504} else 502
        raise HTTPException(mapped_status, str(error)) from error


@router.get("/artifacts/{artifact_id}/download", tags=["artifacts"])
async def download_artifact(artifact_id: str, session: SessionDep, storage: StorageDep):
    artifact = fetch_or_404(session, Artifact, artifact_id)
    if artifact.state == ArtifactState.MISSING:
        raise HTTPException(status.HTTP_410_GONE, "Artifact content was purged and can be regenerated")
    path = storage.resolve(artifact.storage_key)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Artifact content is missing")
    if path.is_dir():
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Directory bundles must be accessed through their managed storage location",
        )
    async def content_stream():
        with path.open("rb") as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                yield chunk

    encoded_filename = quote(artifact.original_filename)
    return StreamingResponse(
        content_stream(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": "attachment", "X-Spectarr-Filename": encoded_filename},
    )


@router.get("/artifacts/{artifact_id}/location", tags=["artifacts"])
async def artifact_location(
    artifact_id: str,
    session: SessionDep,
    storage: StorageDep,
    _worker_auth: WorkerAuthDep,
) -> dict:
    artifact = fetch_or_404(session, Artifact, artifact_id)
    if artifact.state == ArtifactState.MISSING:
        raise HTTPException(status.HTTP_410_GONE, "Artifact content was purged and can be regenerated")
    if not artifact.library_path:
        raise HTTPException(status.HTTP_409_CONFLICT, "Artifact has not been materialized in the library")
    path = storage.resolve_library(artifact.library_path)
    if not path.exists():
        raise HTTPException(status.HTTP_410_GONE, "Artifact library content is missing")
    return {
        "artifact_id": artifact.id,
        "path": str(path),
        "relative_path": path.relative_to(storage.root).as_posix()
        if path.is_relative_to(storage.root)
        else artifact.library_path,
        "library_path": artifact.library_path,
        "filename": artifact.original_filename,
        "is_bundle": path.is_dir(),
        "sha256": artifact.sha256,
    }


@router.post(
    "/runs/{run_id}/artifacts/upload",
    response_model=ArtifactRead,
    status_code=status.HTTP_201_CREATED,
    tags=["artifacts"],
)
async def upload_artifact(
    run_id: str,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
    file: Annotated[UploadFile, File()],
    role: Annotated[ArtifactRole, Form()] = ArtifactRole.SOURCE,
    format: Annotated[str | None, Form()] = None,
    parent_artifact_id: Annotated[str | None, Form()] = None,
    recipe_id: Annotated[str | None, Form()] = None,
    recipe_fingerprint_value: Annotated[str | None, Form(alias="recipe_fingerprint")] = None,
    expected_sha256: Annotated[str | None, Form()] = None,
    metadata_json_value: Annotated[str | None, Form(alias="metadata_json")] = None,
    x_spectarr_worker_token: Annotated[str | None, Header()] = None,
) -> Artifact:
    fetch_or_404(session, Run, run_id)
    if role == ArtifactRole.DERIVED:
        await require_worker_token(settings, request, x_spectarr_worker_token)
    if file.size is not None and file.size > settings.max_upload_bytes:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Upload exceeds configured size limit")
    validate_derivation_links(session, run_id, parent_artifact_id, recipe_id)
    stored = storage.ingest_stream(file.file)
    if expected_sha256 and not secrets.compare_digest(stored.sha256, expected_sha256.lower()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Uploaded artifact checksum does not match")
    try:
        metadata_json = json.loads(metadata_json_value) if metadata_json_value else {}
    except json.JSONDecodeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "metadata_json must be valid JSON") from error
    if not isinstance(metadata_json, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "metadata_json must be an object")
    return create_artifact_record(
        session,
        storage=storage,
        run_id=run_id,
        stored=stored,
        filename=file.filename or "unnamed",
        role=role,
        artifact_format=format or infer_format(file.filename or ""),
        parent_artifact_id=parent_artifact_id,
        recipe_id=recipe_id,
        recipe_fingerprint=recipe_fingerprint_value,
        metadata_json=metadata_json,
    )


@router.post(
    "/runs/{run_id}/artifacts/import",
    response_model=ArtifactRead,
    status_code=status.HTTP_201_CREATED,
    tags=["artifacts"],
)
async def import_artifact(
    run_id: str,
    payload: PathImportRequest,
    session: SessionDep,
    storage: StorageDep,
    settings: SettingsDep,
) -> Artifact:
    fetch_or_404(session, Run, run_id)
    source = require_allowed_import_path(payload.source_path, settings)
    validate_derivation_links(session, run_id, payload.parent_artifact_id, payload.recipe_id)
    try:
        stored = storage.ingest_path(source)
    except (OSError, ValueError) as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    return create_artifact_record(
        session,
        storage=storage,
        run_id=run_id,
        stored=stored,
        filename=source.name,
        role=payload.role,
        artifact_format=payload.format or infer_format(source.name),
        parent_artifact_id=payload.parent_artifact_id,
        recipe_id=payload.recipe_id,
        recipe_fingerprint=payload.recipe_fingerprint,
        metadata_json=payload.metadata_json,
    )


@router.post(
    "/runs/{run_id}/derivatives",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["processing"],
)
async def request_derivative(run_id: str, payload: DerivativeRequest, session: SessionDep) -> Job:
    fetch_or_404(session, Run, run_id)
    artifact = (
        fetch_or_404(session, Artifact, payload.input_artifact_id)
        if payload.input_artifact_id
        else session.scalar(
            select(Artifact)
            .where(
                Artifact.run_id == run_id,
                Artifact.role == ArtifactRole.SOURCE,
                Artifact.state == ArtifactState.READY,
            )
            .order_by(Artifact.created_at.desc())
        )
    )
    if artifact is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Run has no ready source artifact")
    if artifact.run_id != run_id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Input artifact belongs to a different run")
    recipe = fetch_or_404(session, ConversionRecipe, payload.recipe_id) if payload.recipe_id else None
    if recipe is None and payload.format:
        recipe = builtin_profile_for_format(session, payload.format)
        recipe = recipe or session.scalar(
            select(ConversionRecipe).where(ConversionRecipe.name == f"default-{payload.format.lower()}")
        )
        if recipe is None:
            recipe = commit_or_conflict(
                session,
                ConversionRecipe(
                    name=f"default-{payload.format.lower()}",
                    description=f"Default {payload.format} conversion profile.",
                    converter="msconvert",
                    output_format=payload.format,
                    parameters={
                        "filters": [],
                        "mz_precision": 64,
                        "intensity_precision": 32,
                        "compression": "zlib",
                        "indexed": True,
                    },
                ),
            )
    if recipe is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A valid recipe is required")
    if not recipe.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, "Conversion recipe is disabled")
    fingerprint = recipe_fingerprint(artifact.sha256, recipe, payload.parameters)
    existing_artifact = session.scalar(
        select(Artifact).where(
            Artifact.run_id == run_id,
            Artifact.recipe_fingerprint == fingerprint,
            Artifact.state == ArtifactState.READY,
        )
    )
    if existing_artifact:
        return commit_or_conflict(
            session,
            Job(
                kind="convert",
                state=JobState.SUCCEEDED,
                input_artifact_id=artifact.id,
                output_artifact_id=existing_artifact.id,
                recipe_id=recipe.id,
                progress=1.0,
                parameters={
                    "recipe_fingerprint": fingerprint,
                    "recipe_revision": recipe.revision,
                    "recipe_snapshot": recipe_snapshot(recipe),
                    **payload.parameters,
                },
                finished_at=datetime.now(timezone.utc),
            ),
        )
    existing_job = session.scalar(
        select(Job).where(
            Job.state.in_([JobState.QUEUED, JobState.RUNNING]),
            Job.input_artifact_id == artifact.id,
            Job.recipe_id == recipe.id,
        )
    )
    if existing_job and existing_job.parameters.get("recipe_fingerprint") == fingerprint:
        return existing_job
    return commit_or_conflict(
        session,
        Job(
            kind="convert",
            input_artifact_id=artifact.id,
            recipe_id=recipe.id,
            parameters={
                "recipe_fingerprint": fingerprint,
                "recipe_revision": recipe.revision,
                "recipe_snapshot": recipe_snapshot(recipe),
                **payload.parameters,
            },
        ),
    )


@router.post("/jobs", response_model=JobRead, status_code=status.HTTP_201_CREATED, tags=["jobs"])
async def create_job(payload: JobCreate, session: SessionDep) -> Job:
    if payload.input_artifact_id:
        fetch_or_404(session, Artifact, payload.input_artifact_id)
    if payload.recipe_id:
        fetch_or_404(session, ConversionRecipe, payload.recipe_id)
    return commit_or_conflict(session, Job(**payload.model_dump()))


@router.get("/jobs", tags=["jobs"])
async def list_jobs(
    session: SessionDep,
    state: JobState | None = None,
    kind: str | None = None,
    offset: int = 0,
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    query = select(Job).order_by(Job.created_at.desc())
    if state:
        query = query.where(Job.state == state)
    if kind:
        query = query.where(Job.kind == kind)
    return [job_view(job) for job in session.scalars(query.offset(offset).limit(limit))]


@router.get("/jobs/{job_id}", response_model=JobRead, tags=["jobs"])
async def get_job(job_id: str, session: SessionDep) -> Job:
    return fetch_or_404(session, Job, job_id)


@router.post("/jobs/{job_id}/claim", response_model=JobRead, tags=["jobs"])
async def claim_job(
    job_id: str,
    session: SessionDep,
    settings: SettingsDep,
    _worker_auth: WorkerAuthDep,
    x_spectarr_worker_id: Annotated[str | None, Header()] = None,
) -> Job:
    now = datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(seconds=max(30, settings.job_lease_seconds))
    result = session.execute(
        update(Job)
        .where(
            Job.id == job_id,
            Job.attempts < Job.max_attempts,
            or_(
                Job.state == JobState.QUEUED,
                and_(Job.state == JobState.RUNNING, Job.lease_expires_at < now),
            ),
        )
        .values(
            state=JobState.RUNNING,
            attempts=Job.attempts + 1,
            started_at=now,
            worker_id=x_spectarr_worker_id or "anonymous-worker",
            lease_expires_at=lease_expires_at,
        )
    )
    if result.rowcount != 1:
        session.rollback()
        fetch_or_404(session, Job, job_id)
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not claimable")
    session.commit()
    return fetch_or_404(session, Job, job_id)


@router.post("/jobs/{job_id}/heartbeat", response_model=JobRead, tags=["jobs"])
async def heartbeat_job(
    job_id: str,
    session: SessionDep,
    settings: SettingsDep,
    _worker_auth: WorkerAuthDep,
    x_spectarr_worker_id: Annotated[str | None, Header()] = None,
) -> Job:
    job = fetch_or_404(session, Job, job_id)
    worker_id = x_spectarr_worker_id or "anonymous-worker"
    if job.state != JobState.RUNNING or job.worker_id != worker_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not leased to this worker")
    job.lease_expires_at = datetime.now(timezone.utc) + timedelta(seconds=max(30, settings.job_lease_seconds))
    session.commit()
    session.refresh(job)
    return job


@router.patch("/jobs/{job_id}", response_model=JobRead, tags=["jobs"])
async def update_job(
    job_id: str,
    payload: JobUpdate,
    session: SessionDep,
    _worker_auth: WorkerAuthDep,
    x_spectarr_worker_id: Annotated[str | None, Header()] = None,
) -> Job:
    job = fetch_or_404(session, Job, job_id)
    worker_id = x_spectarr_worker_id or "anonymous-worker"
    if job.state == JobState.RUNNING and job.worker_id != worker_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job is not leased to this worker")
    values = payload.model_dump(exclude_unset=True)
    if (
        payload.state == JobState.SUCCEEDED
        and job.kind in {"convert", JobKind.CONVERT}
        and not payload.output_artifact_id
        and not job.output_artifact_id
    ):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "A conversion job requires an output artifact")
    if payload.output_artifact_id:
        output = fetch_or_404(session, Artifact, payload.output_artifact_id)
        if job.input_artifact and output.run_id != job.input_artifact.run_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Output artifact belongs to a different run")
    for key, value in values.items():
        setattr(job, key, value)
    now = datetime.now(timezone.utc)
    if payload.state == JobState.RUNNING and job.started_at is None:
        job.started_at = now
    if payload.state in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}:
        job.finished_at = now
        job.lease_expires_at = None
        if payload.state == JobState.SUCCEEDED:
            job.progress = 1.0
    session.commit()
    session.refresh(job)
    return job


@router.post("/jobs/{job_id}/retry", response_model=JobRead, tags=["jobs"])
async def retry_job(job_id: str, session: SessionDep) -> Job:
    job = fetch_or_404(session, Job, job_id)
    if job.state not in {JobState.FAILED, JobState.CANCELLED}:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only failed or cancelled jobs can be retried")
    if job.attempts >= job.max_attempts:
        raise HTTPException(status.HTTP_409_CONFLICT, "Job has exhausted its attempts")
    job.state = JobState.QUEUED
    job.error = None
    job.progress = 0
    job.started_at = None
    job.finished_at = None
    job.worker_id = None
    job.lease_expires_at = None
    session.commit()
    session.refresh(job)
    return job


@router.post(
    "/runs/{run_id}/annotations",
    response_model=AnnotationRead,
    status_code=status.HTTP_201_CREATED,
    tags=["runs"],
)
async def create_annotation(run_id: str, payload: AnnotationCreate, session: SessionDep) -> RunAnnotation:
    fetch_or_404(session, Run, run_id)
    return commit_or_conflict(session, RunAnnotation(run_id=run_id, **payload.model_dump()))


@router.get("/runs/{run_id}/annotations", response_model=list[AnnotationRead], tags=["runs"])
async def list_annotations(run_id: str, session: SessionDep) -> list[RunAnnotation]:
    fetch_or_404(session, Run, run_id)
    return list(
        session.scalars(
            select(RunAnnotation).where(RunAnnotation.run_id == run_id).order_by(RunAnnotation.created_at)
        )
    )


def create_artifact_record(
    session: Session,
    *,
    storage: LocalArtifactStorage,
    run_id: str,
    stored: StoredObject,
    filename: str,
    role: ArtifactRole,
    artifact_format: str,
    parent_artifact_id: str | None,
    recipe_id: str | None,
    recipe_fingerprint: str | None = None,
    metadata_json: dict | None = None,
) -> Artifact:
    artifact = Artifact(
        run_id=run_id,
        parent_artifact_id=parent_artifact_id,
        recipe_id=recipe_id,
        role=role,
        state=ArtifactState.READY,
        format=artifact_format,
        original_filename=filename,
        storage_key=stored.key,
        byte_size=stored.byte_size,
        sha256=stored.sha256,
        bundle_manifest=stored.manifest,
        recipe_fingerprint=recipe_fingerprint,
        metadata_json=metadata_json or {},
        immutable=True,
    )
    try:
        session.add(artifact)
        session.flush()
        materializer = LibraryMaterializer(storage)
        materializer.materialize_artifact(artifact)
        materializer.write_catalog(session)
        session.commit()
        session.refresh(artifact)
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A record with these values already exists") from error
    schedule_source_pipeline(session, artifact)
    session.refresh(artifact)
    return artifact


def validate_derivation_links(
    session: Session, run_id: str, parent_artifact_id: str | None, recipe_id: str | None
) -> None:
    if parent_artifact_id:
        parent = fetch_or_404(session, Artifact, parent_artifact_id)
        if parent.run_id != run_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Parent artifact belongs to a different run")
    if recipe_id:
        fetch_or_404(session, ConversionRecipe, recipe_id)


def require_allowed_import_path(raw_path: str, settings: Settings) -> Path:
    try:
        source = Path(raw_path).expanduser().resolve(strict=True)
    except OSError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Import source does not exist") from error
    roots = [root.expanduser().resolve(strict=False) for root in settings.import_roots]
    if not roots or not any(source.is_relative_to(root) for root in roots):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Import source is outside configured import roots")
    return source


def infer_format(filename: str) -> str:
    lower = filename.lower()
    known_suffixes = {
        ".mzml.gz": "mzML",
        ".mzml": "mzML",
        ".mzxml": "mzXML",
        ".mgf.gz": "MGF",
        ".mgf": "MGF",
        ".ms2.gz": "MS2",
        ".ms2": "MS2",
        ".msp.gz": "MSP",
        ".msp": "MSP",
        ".wiff": "WIFF",
        ".raw": "RAW",
        ".d": "vendor_directory",
    }
    for suffix, format_name in known_suffixes.items():
        if lower.endswith(suffix):
            return format_name
    return "unknown"


def recipe_fingerprint(source_sha256: str, recipe: ConversionRecipe, overrides: dict) -> str:
    payload = {
        "source_sha256": source_sha256,
        "converter": recipe.converter,
        "converter_version": recipe.converter_version,
        "output_format": recipe.output_format,
        "parameters": recipe.parameters,
        "overrides": overrides,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def artifact_view(artifact: Artifact) -> dict:
    status_name = {
        ArtifactState.READY: "verified",
        ArtifactState.STAGING: "generating",
        ArtifactState.VALIDATING: "generating",
        ArtifactState.FAILED: "failed",
        ArtifactState.QUARANTINED: "failed",
        ArtifactState.MISSING: "purged",
        "ready": "verified",
        "staging": "generating",
        "validating": "generating",
        "failed": "failed",
        "quarantined": "failed",
        "missing": "purged",
    }.get(artifact.state, "failed")
    return {
        "id": artifact.id,
        "name": artifact.original_filename,
        "format": dashboard_format(artifact.format),
        "role": artifact.role,
        "sizeBytes": artifact.byte_size,
        "checksum": f"sha256:{artifact.sha256}",
        "status": status_name,
        "libraryPath": artifact.library_path,
        "materializationMode": artifact.materialization_mode,
    }


def run_view(run: Run) -> dict:
    artifacts = list(run.artifacts)
    source = next(
        (
            artifact
            for artifact in artifacts
            if artifact.role == ArtifactRole.SOURCE and artifact.state == ArtifactState.READY
        ),
        None,
    )
    extraction_results = [result for artifact in artifacts for result in artifact.extraction_results]
    latest_extraction = max(extraction_results, key=lambda result: result.created_at, default=None)
    latest_jobs: dict[tuple[str, str, str | None], Job] = {}
    for artifact in artifacts:
        for job in artifact.jobs_as_input:
            key = (artifact.id, str(job.kind), job.recipe_id)
            previous = latest_jobs.get(key)
            if previous is None or job.updated_at > previous.updated_at:
                latest_jobs[key] = job
    active_jobs = [
        job
        for job in latest_jobs.values()
        if job.state in {JobState.QUEUED, JobState.RUNNING, "queued", "running"}
    ]
    failed_jobs = [
        job for job in latest_jobs.values() if job.state in {JobState.FAILED, "failed"}
    ]
    if active_jobs:
        run_status = "processing"
    elif failed_jobs and latest_extraction is None:
        run_status = "failed"
    elif failed_jobs:
        run_status = "warning"
    else:
        run_status = "ready" if source else "warning"
    metadata = run.metadata_json or {}
    sample_links = [
        {
            "id": link.id,
            "sample_id": link.sample_id,
            "sample_name": link.sample.name,
            "position": link.position,
            "label": link.label,
            "role": link.role,
            "metadata_json": link.metadata_json,
        }
        for link in run.sample_links
    ]
    if not sample_links and run.sample:
        sample_links = [
            {
                "id": None,
                "sample_id": run.sample.id,
                "sample_name": run.sample.name,
                "position": 0,
                "label": "label free sample",
                "role": "analyte",
                "metadata_json": {},
            }
        ]
    return {
        "id": run.id,
        "name": run.name,
        "experiment_id": run.experiment_id,
        "projectId": run.experiment.project_id,
        "sample_id": run.sample_id,
        "sample_links": sample_links,
        "instrument_id": run.instrument_id,
        "source_class": run.source_class,
        "metadata_json": metadata,
        "assignment_status": run.assignment_status,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
        "projectName": run.experiment.project.name,
        "experimentName": run.experiment.name,
        "sampleName": ", ".join(link["sample_name"] for link in sample_links) or "Unassigned",
        "instrument": run.instrument.name if run.instrument else "Unknown",
        "acquiredAt": (run.acquired_at or run.created_at).isoformat(),
        "importedAt": run.created_at.isoformat(),
        "status": run_status,
        "sourceFormat": dashboard_format(source.format) if source else "RAW",
        "sizeBytes": sum(
            artifact.byte_size for artifact in artifacts if artifact.state == ArtifactState.READY
        ),
        "spectraCount": metadata.get("spectra_count"),
        "ms2Count": metadata.get("ms2_count"),
        "durationMinutes": metadata.get("duration_minutes"),
        "artifacts": [artifact_view(artifact) for artifact in artifacts],
        "latest_extraction": (
            {
                "id": latest_extraction.id,
                "schema_version": latest_extraction.schema_version,
                "extractor": latest_extraction.extractor,
                "extractor_version": latest_extraction.extractor_version,
                "result_type": latest_extraction.result_type,
                "payload": latest_extraction.payload,
                "warnings": latest_extraction.warnings,
                "created_at": latest_extraction.created_at.isoformat(),
            }
            if latest_extraction
            else None
        ),
    }


def project_view(project: Project) -> dict:
    runs = [run for experiment in project.experiments for run in experiment.runs]
    return {
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "system_key": project.system_key,
        "metadata_json": project.metadata_json,
        "sdrf": (
            {
                "status": project.sdrf_document.status,
                "revision": project.sdrf_document.revision,
                "row_count": len(project.sdrf_document.rows),
                "source_filename": project.sdrf_document.source_filename,
            }
            if project.sdrf_document
            else None
        ),
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "runCount": len(runs),
        "sizeBytes": sum(
            artifact.byte_size
            for run in runs
            for artifact in run.artifacts
            if artifact.state == ArtifactState.READY
        ),
        "updatedAt": project.updated_at.isoformat(),
    }


def assign_runs(
    session: Session,
    storage: LocalArtifactStorage,
    request: Request,
    runs: list[Run],
    experiment_id: str,
    sample_id: str | None,
) -> list[Run]:
    principal = request.state.principal
    if principal.user_id is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "A user credential is required to assign runs")
    destination = fetch_or_404(session, Experiment, experiment_id)
    if destination.project.system_key:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Runs cannot be assigned to a system inbox")
    sample = fetch_or_404(session, Sample, sample_id) if sample_id else None
    if sample and sample.experiment_id != destination.id:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Sample belongs to a different experiment")

    materializer = LibraryMaterializer(storage)
    old_projects: set[Project] = set()
    old_paths: list[str] = []
    new_paths: list[str] = []
    old_manifests: list[str] = []
    try:
        for run in runs:
            old_project = run.experiment.project
            old_projects.add(old_project)
            old_manifests.append(materializer.run_manifest_key(run))
            previous_experiment_id = run.experiment_id
            previous_project_id = old_project.id
            for artifact in run.artifacts:
                if artifact.library_path:
                    old_paths.append(artifact.library_path)
                artifact.library_path = None
                artifact.materialization_mode = None
            run.experiment = destination
            run.sample = sample
            run.sample_links.clear()
            if sample:
                run.sample_links.append(RunSample(sample=sample, position=0))
            run.assignment_status = "assigned"
            session.flush()
            for artifact in run.artifacts:
                new_paths.append(materializer.materialize_artifact(artifact))
            session.add(
                AuditLog(
                    actor_type=principal.actor_type,
                    actor_id=principal.actor_id,
                    action="run.assigned",
                    resource_type="run",
                    resource_id=run.id,
                    project_id=destination.project_id,
                    details={
                        "from_project_id": previous_project_id,
                        "from_experiment_id": previous_experiment_id,
                        "to_project_id": destination.project_id,
                        "to_experiment_id": destination.id,
                        "sample_id": sample.id if sample else None,
                    },
                    request_id=request.headers.get("X-Request-ID"),
                )
            )
        session.commit()
    except Exception:
        session.rollback()
        for path in new_paths:
            storage.remove_library_key(path)
        for project in old_projects | {destination.project}:
            materializer.write_project_manifest(project)
        materializer.write_catalog(session)
        raise

    for path in old_paths:
        if path not in new_paths:
            storage.remove_library_key(path)
    for manifest in old_manifests:
        current_manifests = {materializer.run_manifest_key(run) for run in runs}
        if manifest not in current_manifests:
            storage.remove_library_key(manifest)
    for run in runs:
        session.refresh(run)
        materializer.write_run_manifest(run)
        for artifact in run.artifacts:
            if artifact.role == ArtifactRole.SOURCE and artifact.state == ArtifactState.READY:
                schedule_source_pipeline(session, artifact)
    for project in old_projects | {destination.project}:
        materializer.write_project_manifest(project)
    materializer.write_catalog(session)
    return runs


def job_view(job: Job) -> dict:
    state = job.state.value if isinstance(job.state, JobState) else str(job.state)
    frontend_state = "complete" if state == "succeeded" else state
    return {
        "id": job.id,
        "kind": job.kind,
        "state": job.state,
        "status": frontend_state,
        "input_artifact_id": job.input_artifact_id,
        "output_artifact_id": job.output_artifact_id,
        "recipe_id": job.recipe_id,
        "runName": job.input_artifact.run.name if job.input_artifact else "System",
        "progress": round(job.progress * 100),
        "detail": job.error or job.parameters.get("detail", job.kind),
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "parameters": job.parameters,
        "error": job.error,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
        "createdAt": job.created_at.isoformat(),
    }


def dashboard_format(format_name: str) -> str:
    normalized = format_name.lower()
    return {
        "raw": "RAW",
        "wiff": "RAW",
        "vendor_directory": "RAW",
        "mzml": "mzML",
        "mzxml": "mzXML",
        "mgf": "MGF",
        "ms2": "MS2",
        "msp": "MSP",
        "parquet": "Parquet",
    }.get(normalized, format_name)
