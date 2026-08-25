from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .library import LibraryMaterializer
from .models import (
    Agent,
    Artifact,
    ArtifactRole,
    ArtifactState,
    AuditLog,
    Experiment,
    Job,
    JobState,
    Project,
    Run,
    Sample,
)
from .schemas import DerivedPurgePreviewRequest
from .storage import LocalArtifactStorage


def experiment_deletion_preview(session: Session, experiment: Experiment) -> dict:
    artifacts = artifacts_for_experiment(session, experiment.id)
    return {
        "experiment_id": experiment.id,
        "experiment_name": experiment.name,
        "run_count": len(experiment.runs),
        "source_count": sum(artifact.role == ArtifactRole.SOURCE for artifact in artifacts),
        "derived_count": sum(artifact.role == ArtifactRole.DERIVED for artifact in artifacts),
        "logical_bytes": sum(artifact.byte_size for artifact in artifacts if artifact.state == ArtifactState.READY),
    }


def delete_experiment(
    session: Session,
    storage: LocalArtifactStorage,
    experiment: Experiment,
    confirmation: str,
    actor_type: str,
    actor_id: str | None,
) -> dict:
    if confirmation != experiment.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Confirmation must match the experiment name")
    if experiment.intake_agent_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Instrument inbox experiments cannot be deleted")
    assigned_agent = session.scalar(
        select(Agent).where(Agent.destination_experiment_id == experiment.id, Agent.enabled.is_(True))
    )
    if assigned_agent:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Agent '{assigned_agent.name}' must be retargeted before this experiment can be deleted",
        )
    artifacts = artifacts_for_experiment(session, experiment.id)
    artifact_ids = [artifact.id for artifact in artifacts]
    if artifact_ids and session.scalar(
        select(Job).where(
            Job.input_artifact_id.in_(artifact_ids),
            Job.state.in_([JobState.QUEUED, JobState.RUNNING]),
        )
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Cancel active processing jobs before deleting this experiment")
    project_id = experiment.project_id
    preview = experiment_deletion_preview(session, experiment)
    materializer = LibraryMaterializer(storage)
    library_paths = [artifact.library_path for artifact in artifacts if artifact.library_path]
    storage_keys = {artifact.storage_key for artifact in artifacts}
    run_manifest_paths = [materializer.run_manifest_key(run) for run in experiment.runs]
    run_ids = [run.id for run in experiment.runs]
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action="experiment.deleted",
            resource_type="experiment",
            resource_id=experiment.id,
            project_id=project_id,
            details=preview,
        )
    )
    if run_ids:
        session.execute(delete(Run).where(Run.id.in_(run_ids)))
    session.execute(delete(Sample).where(Sample.experiment_id == experiment.id))
    session.execute(delete(Experiment).where(Experiment.id == experiment.id))
    session.commit()

    for path in library_paths + run_manifest_paths:
        storage.remove_library_key(path)
    for storage_key in storage_keys:
        if not session.scalar(
            select(Artifact).where(
                Artifact.storage_key == storage_key,
                Artifact.state == ArtifactState.READY,
            )
        ):
            storage.remove_object(storage_key)
    session.expire_all()
    project = session.get(Project, project_id)
    if project:
        materializer.write_project_manifest(project)
    materializer.write_catalog(session)
    return preview


def preview_derived_purge(session: Session, payload: DerivedPurgePreviewRequest) -> dict:
    candidates, blocked = derived_purge_candidates(session, payload)
    counts = Counter(str(artifact.format) for artifact in candidates)
    return {
        "artifact_count": len(candidates),
        "reclaimable_bytes": sum(artifact.byte_size for artifact in candidates),
        "format_counts": dict(sorted(counts.items())),
        "blocked_count": len(blocked),
    }


def purge_derived_artifacts(
    session: Session,
    storage: LocalArtifactStorage,
    payload: DerivedPurgePreviewRequest,
    actor_type: str,
    actor_id: str | None,
) -> dict:
    candidates, blocked = derived_purge_candidates(session, payload)
    materializer = LibraryMaterializer(storage)
    preview = preview_derived_purge(session, payload)
    now = datetime.now(timezone.utc).isoformat()
    library_paths = [artifact.library_path for artifact in candidates if artifact.library_path]
    storage_keys = {artifact.storage_key for artifact in candidates}
    runs = {artifact.run for artifact in candidates}
    projects = {run.experiment.project for run in runs}
    for artifact in candidates:
        artifact.state = ArtifactState.MISSING
        artifact.library_path = None
        artifact.materialization_mode = None
        artifact.metadata_json = {
            **artifact.metadata_json,
            "purged_at": now,
            "purge_reason": "regenerable derivative storage reclamation",
        }
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action="derived_artifacts.purged",
            resource_type="artifact",
            details={
                **preview,
                "scope_type": payload.scope_type,
                "scope_ids": payload.scope_ids,
                "formats": payload.formats,
            },
        )
    )
    session.commit()

    for path in library_paths:
        storage.remove_library_key(path)
    for storage_key in storage_keys:
        if not session.scalar(
            select(Artifact).where(
                Artifact.storage_key == storage_key,
                Artifact.state == ArtifactState.READY,
            )
        ):
            storage.remove_object(storage_key)
    for run in runs:
        materializer.write_run_manifest(run)
    for project in projects:
        materializer.write_project_manifest(project)
    materializer.write_catalog(session)
    return {**preview, "blocked_count": len(blocked)}


def derived_purge_candidates(
    session: Session,
    payload: DerivedPurgePreviewRequest,
) -> tuple[list[Artifact], list[Artifact]]:
    run_ids = resolve_run_ids(session, payload)
    normalized_formats = {value.casefold() for value in payload.formats}
    artifacts = list(
        session.scalars(
            select(Artifact).where(
                Artifact.run_id.in_(run_ids),
                Artifact.role == ArtifactRole.DERIVED,
                Artifact.state == ArtifactState.READY,
            )
        )
    )
    artifacts = [artifact for artifact in artifacts if str(artifact.format).casefold() in normalized_formats]
    artifact_ids = [artifact.id for artifact in artifacts]
    active_input_ids = set()
    if artifact_ids:
        active_input_ids = set(
            session.scalars(
                select(Job.input_artifact_id).where(
                    Job.input_artifact_id.in_(artifact_ids),
                    Job.state.in_([JobState.QUEUED, JobState.RUNNING]),
                )
            )
        )
    blocked = [artifact for artifact in artifacts if artifact.id in active_input_ids]
    return [artifact for artifact in artifacts if artifact.id not in active_input_ids], blocked


def resolve_run_ids(session: Session, payload: DerivedPurgePreviewRequest) -> list[str]:
    if payload.scope_type == "project":
        project = session.get(Project, payload.scope_ids[0])
        if project is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        experiment_ids = select(Experiment.id).where(Experiment.project_id == project.id)
        return list(session.scalars(select(Run.id).where(Run.experiment_id.in_(experiment_ids))))
    if payload.scope_type == "experiments":
        found = set(session.scalars(select(Experiment.id).where(Experiment.id.in_(payload.scope_ids))))
        if found != set(payload.scope_ids):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more experiments were not found")
        return list(session.scalars(select(Run.id).where(Run.experiment_id.in_(payload.scope_ids))))
    found = set(session.scalars(select(Run.id).where(Run.id.in_(payload.scope_ids))))
    if found != set(payload.scope_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more runs were not found")
    return list(found)


def artifacts_for_experiment(session: Session, experiment_id: str) -> list[Artifact]:
    run_ids = select(Run.id).where(Run.experiment_id == experiment_id)
    return list(session.scalars(select(Artifact).where(Artifact.run_id.in_(run_ids))))
