from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Artifact,
    ArtifactRole,
    ArtifactState,
    AutomationRule,
    AutomationScope,
    ConversionRecipe,
    Experiment,
    Job,
    JobKind,
    JobState,
    ProcessingBatch,
    ProcessingBatchItem,
    Project,
    Run,
)
from .schemas import ProcessingBatchRequest


BUILTIN_PROFILES = (
    {
        "name": "Standard mzML",
        "description": "Archival mzML with indexed spectra, zlib compression, 64-bit m/z, and 32-bit intensities.",
        "output_format": "mzML",
        "parameters": {
            "filters": [],
            "mz_precision": 64,
            "intensity_precision": 32,
            "compression": "zlib",
            "indexed": True,
        },
    },
    {
        "name": "Standard MGF",
        "description": "Centroided MS2 peak lists for database and spectral search tools.",
        "output_format": "MGF",
        "parameters": {
            "filters": [
                {"kind": "peak_picking", "algorithm": "vendor", "ms_levels": [1, 2]},
                {"kind": "ms_level", "levels": [2]},
            ],
            "mz_precision": 64,
            "intensity_precision": 32,
            "compression": "none",
            "indexed": True,
        },
    },
)


def ensure_builtin_profiles(session: Session) -> list[ConversionRecipe]:
    profiles: list[ConversionRecipe] = []
    changed = False
    for definition in BUILTIN_PROFILES:
        profile = session.scalar(
            select(ConversionRecipe).where(ConversionRecipe.name == definition["name"])
        )
        if profile is None:
            profile = ConversionRecipe(
                **definition,
                converter="msconvert",
                revision=1,
                system=True,
                enabled=True,
            )
            session.add(profile)
            session.flush()
            changed = True
        profiles.append(profile)
    existing_rules = list(session.scalars(select(AutomationRule)))
    profiles_by_format = {str(profile.output_format).casefold(): profile for profile in profiles}
    for rule in existing_rules:
        migrated_actions = []
        migrated = False
        for action in rule.actions:
            updated_action = dict(action)
            if updated_action.get("kind") == JobKind.CONVERT.value:
                parameters = dict(updated_action.get("parameters") or {})
                output_format = parameters.get("format") or updated_action.get("format")
                current_recipe_id = updated_action.get("recipe_id")
                current_recipe = (
                    session.get(ConversionRecipe, current_recipe_id)
                    if current_recipe_id
                    else None
                )
                if current_recipe and current_recipe.name.startswith("default-"):
                    output_format = current_recipe.output_format
                replacement = profiles_by_format.get(str(output_format).casefold())
                if replacement and updated_action.get("recipe_id") != replacement.id:
                    updated_action["recipe_id"] = replacement.id
                    migrated = True
            migrated_actions.append(updated_action)
        if migrated:
            rule.actions = migrated_actions
            changed = True
    if not existing_rules:
        mzml = next(profile for profile in profiles if profile.output_format == "mzML")
        session.add(
            AutomationRule(
                name="Default archival mzML",
                enabled=True,
                scope=AutomationScope.GLOBAL,
                trigger="source_artifact_ready",
                actions=[{"kind": JobKind.CONVERT.value, "recipe_id": mzml.id}],
                priority=100,
            )
        )
        changed = True
    if changed:
        session.commit()
        for profile in profiles:
            session.refresh(profile)
    return profiles


def builtin_profile_for_format(session: Session, output_format: str) -> ConversionRecipe | None:
    profiles = ensure_builtin_profiles(session)
    return next(
        (
            profile
            for profile in profiles
            if str(profile.output_format).casefold() == str(output_format).casefold()
        ),
        None,
    )


@dataclass
class Target:
    run: Run
    source: Artifact
    recipe: ConversionRecipe
    disposition: str
    reason: str
    fingerprint: str
    existing_job: Job | None = None


def recipe_snapshot(recipe: ConversionRecipe) -> dict:
    return {
        "id": recipe.id,
        "name": recipe.name,
        "description": recipe.description,
        "converter": recipe.converter,
        "converter_version": recipe.converter_version,
        "output_format": recipe.output_format,
        "parameters": recipe.parameters,
        "revision": recipe.revision,
    }


def profile_fingerprint(source_sha256: str, recipe: ConversionRecipe) -> str:
    from .api import recipe_fingerprint

    return recipe_fingerprint(source_sha256, recipe, {})


def resolve_scope_runs(session: Session, payload: ProcessingBatchRequest) -> list[Run]:
    if payload.scope_type == "project":
        project_id = payload.scope_ids[0]
        if session.get(Project, project_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
        experiment_ids = select(Experiment.id).where(Experiment.project_id == project_id)
        runs = list(
            session.scalars(
                select(Run).where(Run.experiment_id.in_(experiment_ids)).order_by(Run.created_at)
            )
        )
    elif payload.scope_type == "experiments":
        found_ids = set(session.scalars(select(Experiment.id).where(Experiment.id.in_(payload.scope_ids))))
        if len(found_ids) != len(payload.scope_ids):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more experiments were not found")
        runs = list(
            session.scalars(
                select(Run).where(Run.experiment_id.in_(payload.scope_ids)).order_by(Run.created_at)
            )
        )
    else:
        runs = list(session.scalars(select(Run).where(Run.id.in_(payload.scope_ids)).order_by(Run.created_at)))
        if len(runs) != len(payload.scope_ids):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more runs were not found")
    return runs


def plan_batch(session: Session, payload: ProcessingBatchRequest) -> tuple[list[Run], list[Target]]:
    ensure_builtin_profiles(session)
    runs = resolve_scope_runs(session, payload)
    recipes = list(
        session.scalars(select(ConversionRecipe).where(ConversionRecipe.id.in_(payload.recipe_ids)))
    )
    if len(recipes) != len(payload.recipe_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "One or more processing profiles were not found")
    if any(not recipe.enabled for recipe in recipes):
        raise HTTPException(status.HTTP_409_CONFLICT, "Disabled processing profiles cannot be queued")
    targets: list[Target] = []
    for run in runs:
        source = session.scalar(
            select(Artifact)
            .where(
                Artifact.run_id == run.id,
                Artifact.role == ArtifactRole.SOURCE,
                Artifact.state == ArtifactState.READY,
            )
            .order_by(Artifact.created_at.desc())
        )
        if source is None:
            continue
        for recipe in recipes:
            fingerprint = profile_fingerprint(source.sha256, recipe)
            same_format = str(source.format).casefold() == str(recipe.output_format).casefold()
            if same_format:
                targets.append(Target(run, source, recipe, "incompatible", "Source already has this format", fingerprint))
                continue
            current = session.scalar(
                select(Artifact).where(
                    Artifact.run_id == run.id,
                    Artifact.parent_artifact_id == source.id,
                    Artifact.recipe_fingerprint == fingerprint,
                    Artifact.state == ArtifactState.READY,
                )
            )
            if current is not None and payload.mode != "force":
                targets.append(Target(run, source, recipe, "current", "Current output already exists", fingerprint))
                continue
            previous = session.scalar(
                select(Artifact).where(
                    Artifact.run_id == run.id,
                    Artifact.parent_artifact_id == source.id,
                    Artifact.recipe_id == recipe.id,
                    Artifact.state == ArtifactState.READY,
                )
            )
            if previous is not None and payload.mode == "missing":
                targets.append(Target(run, source, recipe, "stale", "An older profile revision exists", fingerprint))
                continue
            existing_jobs = session.scalars(
                select(Job).where(
                    Job.input_artifact_id == source.id,
                    Job.recipe_id == recipe.id,
                    Job.state.in_([JobState.QUEUED, JobState.RUNNING]),
                )
            )
            existing_job = next(
                (job for job in existing_jobs if job.parameters.get("recipe_fingerprint") == fingerprint),
                None,
            )
            if existing_job is not None and payload.mode != "force":
                targets.append(Target(run, source, recipe, "queued", "A matching job is already queued", fingerprint, existing_job))
                continue
            disposition = "force" if payload.mode == "force" else "stale_queue" if previous else "queue"
            reason = "Forced regeneration" if payload.mode == "force" else "Profile revision is outdated" if previous else "Output is missing"
            targets.append(Target(run, source, recipe, disposition, reason, fingerprint))
    return runs, targets


def preview_batch(session: Session, payload: ProcessingBatchRequest) -> dict:
    runs, targets = plan_batch(session, payload)
    return {
        "scope_type": payload.scope_type,
        "run_count": len(runs),
        "target_count": len(targets),
        "queue_count": sum(target.disposition in {"queue", "stale_queue", "force"} for target in targets),
        "current_count": sum(target.disposition == "current" for target in targets),
        "stale_count": sum(target.disposition in {"stale", "stale_queue"} for target in targets),
        "incompatible_count": sum(target.disposition == "incompatible" for target in targets),
        "queued_count": sum(target.disposition == "queued" for target in targets),
    }


def create_processing_batch(
    session: Session,
    payload: ProcessingBatchRequest,
    requested_by: str | None,
) -> ProcessingBatch:
    _runs, targets = plan_batch(session, payload)
    batch = ProcessingBatch(
        scope_type=payload.scope_type,
        scope_ids=payload.scope_ids,
        mode=payload.mode,
        requested_by=requested_by,
        label=payload.label,
    )
    session.add(batch)
    session.flush()
    for target in targets:
        job = target.existing_job
        if target.disposition in {"queue", "stale_queue", "force"}:
            parameters = {
                "recipe_fingerprint": target.fingerprint,
                "recipe_revision": target.recipe.revision,
                "recipe_snapshot": recipe_snapshot(target.recipe),
            }
            if target.disposition == "force":
                parameters["force_nonce"] = secrets.token_hex(8)
            job = Job(
                kind=JobKind.CONVERT,
                input_artifact_id=target.source.id,
                recipe_id=target.recipe.id,
                parameters=parameters,
            )
            session.add(job)
            session.flush()
        session.add(
            ProcessingBatchItem(
                batch_id=batch.id,
                run_id=target.run.id,
                input_artifact_id=target.source.id,
                recipe_id=target.recipe.id,
                job_id=job.id if job else None,
                disposition="queued" if job else "skipped",
                reason=target.reason,
            )
        )
    session.commit()
    session.refresh(batch)
    return batch


def batch_view(batch: ProcessingBatch, include_items: bool = True) -> dict:
    items = [batch_item_view(item) for item in batch.items]
    counts = {state: sum(item["state"] == state for item in items) for state in (
        "queued", "running", "succeeded", "failed", "skipped", "cancelled"
    )}
    total = len(items)
    progress_sum = sum(item["progress"] for item in items)
    if counts["running"]:
        state = "running"
    elif counts["queued"]:
        state = "queued"
    elif counts["failed"]:
        state = "failed"
    elif counts["cancelled"]:
        state = "cancelled"
    else:
        state = "succeeded"
    return {
        "id": batch.id,
        "scope_type": batch.scope_type,
        "scope_ids": batch.scope_ids,
        "mode": batch.mode,
        "requested_by": batch.requested_by,
        "label": batch.label,
        "state": state,
        "total_count": total,
        "queued_count": counts["queued"],
        "running_count": counts["running"],
        "succeeded_count": counts["succeeded"],
        "failed_count": counts["failed"],
        "skipped_count": counts["skipped"],
        "cancelled_count": counts["cancelled"],
        "progress": progress_sum / total if total else 1.0,
        "created_at": batch.created_at,
        "updated_at": batch.updated_at,
        "items": items if include_items else [],
    }


def batch_item_view(item: ProcessingBatchItem) -> dict:
    job = item.job
    state = str(job.state.value if hasattr(job.state, "value") else job.state) if job else "skipped"
    progress = job.progress if job else 1.0
    return {
        "id": item.id,
        "run_id": item.run_id,
        "run_name": item.run.name,
        "input_artifact_id": item.input_artifact_id,
        "recipe_id": item.recipe_id,
        "recipe_name": item.recipe.name,
        "output_format": item.recipe.output_format,
        "job_id": item.job_id,
        "disposition": item.disposition,
        "reason": item.reason,
        "state": state,
        "progress": progress,
        "error": job.error if job else None,
    }
def retry_processing_batch(session: Session, batch: ProcessingBatch) -> ProcessingBatch:
    for item in batch.items:
        job = item.job
        if job is None or job.state not in {JobState.FAILED, JobState.CANCELLED}:
            continue
        if job.attempts < job.max_attempts:
            job.state = JobState.QUEUED
            job.error = None
            job.progress = 0
            job.started_at = None
            job.finished_at = None
            job.worker_id = None
            job.lease_expires_at = None
        else:
            replacement = Job(
                kind=job.kind,
                input_artifact_id=job.input_artifact_id,
                recipe_id=job.recipe_id,
                parameters=job.parameters,
                max_attempts=job.max_attempts,
            )
            session.add(replacement)
            session.flush()
            item.job_id = replacement.id
    session.commit()
    session.refresh(batch)
    return batch


def cancel_processing_batch(session: Session, batch: ProcessingBatch) -> ProcessingBatch:
    now = datetime.now(timezone.utc)
    for item in batch.items:
        if item.job and item.job.state in {JobState.QUEUED, JobState.RUNNING}:
            item.job.state = JobState.CANCELLED
            item.job.finished_at = now
            item.job.lease_expires_at = None
    session.commit()
    session.refresh(batch)
    return batch


def reconcile_processing_rule(session: Session, rule: AutomationRule) -> int:
    if not rule.enabled:
        return 0
    query = (
        select(Artifact)
        .join(Run, Run.id == Artifact.run_id)
        .join(Experiment, Experiment.id == Run.experiment_id)
        .where(
            Artifact.role == ArtifactRole.SOURCE,
            Artifact.state == ArtifactState.READY,
        )
    )
    if rule.scope == AutomationScope.PROJECT:
        query = query.where(Experiment.project_id == rule.project_id)
    elif rule.scope == AutomationScope.INSTRUMENT:
        query = query.where(Run.instrument_id == rule.instrument_id)
    from .pipeline import schedule_source_pipeline

    count = 0
    for artifact in session.scalars(query.order_by(Artifact.created_at)):
        count += len(schedule_source_pipeline(session, artifact))
    return count


def reconcile_profile_rules(session: Session, recipe_id: str) -> int:
    count = 0
    rules = list(session.scalars(select(AutomationRule).where(AutomationRule.enabled.is_(True))))
    for rule in rules:
        if any(action.get("recipe_id") == recipe_id for action in rule.actions):
            count += reconcile_processing_rule(session, rule)
    return count
