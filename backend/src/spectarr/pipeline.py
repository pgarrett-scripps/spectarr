from __future__ import annotations

import hashlib
import json

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import (
    Artifact,
    ArtifactRole,
    ArtifactState,
    AutomationRule,
    AutomationScope,
    ConversionRecipe,
    EventOutbox,
    Job,
    JobKind,
    WebhookDelivery,
    WebhookDestination,
)
from .processing import builtin_profile_for_format, recipe_snapshot


def schedule_source_pipeline(session: Session, artifact: Artifact) -> list[Job]:
    if artifact.state != ArtifactState.READY:
        return []
    run = artifact.run
    project_id = run.experiment.project_id
    event_key = f"artifact.ready:{artifact.id}:{artifact.sha256}"
    if session.scalar(select(EventOutbox).where(EventOutbox.dedupe_key == event_key)) is None:
        event = EventOutbox(
                topic="artifact.ready",
                aggregate_type="artifact",
                aggregate_id=artifact.id,
                dedupe_key=event_key,
                payload={
                    "artifact_id": artifact.id,
                    "run_id": artifact.run_id,
                    "project_id": project_id,
                    "format": artifact.format,
                    "sha256": artifact.sha256,
                },
            )
        session.add(event)
        session.flush()
        enqueue_webhook_deliveries(session, event)
    actions: list[dict] = []
    if artifact.role == ArtifactRole.SOURCE:
        rules = session.scalars(
            select(AutomationRule)
            .where(
                AutomationRule.enabled.is_(True),
                AutomationRule.trigger == "source_artifact_ready",
                or_(
                    AutomationRule.scope == AutomationScope.GLOBAL,
                    AutomationRule.project_id == project_id,
                    AutomationRule.instrument_id == run.instrument_id,
                ),
            )
            .order_by(AutomationRule.priority, AutomationRule.id)
        )
        for rule in rules:
            actions.extend({**action, "automation_rule_id": rule.id} for action in rule.actions)
        extraction_actions = [
            action for action in actions if action.get("kind") == JobKind.EXTRACT_METADATA.value
        ]
        if extraction_actions:
            selected_extraction = dict(extraction_actions[0])
            selected_parameters = dict(selected_extraction.get("parameters", {}))
            selected_parameters["deep_qc"] = any(
                bool(action.get("parameters", {}).get("deep_qc")) for action in extraction_actions
            )
            selected_extraction["parameters"] = selected_parameters
            actions = [
                action for action in actions if action.get("kind") != JobKind.EXTRACT_METADATA.value
            ] + [selected_extraction]
        else:
            actions.append({"kind": JobKind.EXTRACT_METADATA.value, "schema_version": "1.0"})
    elif str(artifact.format).casefold() in {"mzml", "mzxml", "mgf", "ms2"}:
        actions.append({"kind": JobKind.EXTRACT_METADATA.value, "schema_version": "1.0"})
    scheduled: list[Job] = []
    for action in actions:
        kind = action.get("kind")
        if kind not in {item.value for item in JobKind}:
            continue
        recipe_id = action.get("recipe_id")
        conversion_overrides: dict = {}
        if kind == JobKind.CONVERT.value and not recipe_id:
            parameters = action.get("parameters", {})
            output_format = parameters.get("format") or action.get("format")
            if not output_format:
                continue
            if str(output_format).casefold() == str(artifact.format).casefold():
                continue
            recipe_name = f"default-{str(output_format).lower()}"
            recipe = builtin_profile_for_format(session, output_format)
            recipe = recipe or session.scalar(select(ConversionRecipe).where(ConversionRecipe.name == recipe_name))
            if recipe is None:
                recipe = ConversionRecipe(
                    name=recipe_name,
                    converter="msconvert",
                    output_format=output_format,
                    parameters={
                        "filters": [],
                        "mz_precision": 64,
                        "intensity_precision": 32,
                        "compression": "zlib",
                        "indexed": True,
                    },
                )
                session.add(recipe)
                session.flush()
            recipe_id = recipe.id
        if kind == JobKind.CONVERT.value:
            recipe = session.get(ConversionRecipe, recipe_id)
            if recipe is None:
                continue
            if str(recipe.output_format).casefold() == str(artifact.format).casefold():
                continue
            requested = action.get("overrides", {})
            allowed = {"filters", "mz_precision", "intensity_precision", "compression", "indexed"}
            conversion_overrides = {key: value for key, value in requested.items() if key in allowed}
            fingerprint_payload = {
                "source_sha256": artifact.sha256,
                "converter": recipe.converter,
                "converter_version": recipe.converter_version,
                "output_format": recipe.output_format,
                "parameters": recipe.parameters,
                "overrides": conversion_overrides,
            }
            fingerprint_bytes = json.dumps(
                fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            ).encode()
            conversion_overrides["recipe_fingerprint"] = hashlib.sha256(fingerprint_bytes).hexdigest()
            conversion_overrides["recipe_revision"] = recipe.revision
            conversion_overrides["recipe_snapshot"] = recipe_snapshot(recipe)
        canonical = json.dumps(action, sort_keys=True, separators=(",", ":"))
        key = hashlib.sha256(f"{artifact.id}:{artifact.sha256}:{canonical}".encode()).hexdigest()
        if session.scalar(select(Job).where(Job.idempotency_key == key)):
            continue
        job = Job(
            kind=kind,
            input_artifact_id=artifact.id,
            recipe_id=recipe_id,
            parameters=conversion_overrides if kind == JobKind.CONVERT.value else action,
            idempotency_key=key,
        )
        session.add(job)
        scheduled.append(job)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return []
    return scheduled


def enqueue_webhook_deliveries(session: Session, event: EventOutbox) -> None:
    destinations = session.scalars(select(WebhookDestination).where(WebhookDestination.enabled.is_(True)))
    for destination in destinations:
        if destination.event_filters and event.topic not in destination.event_filters:
            continue
        session.add(WebhookDelivery(destination_id=destination.id, event_id=event.id))
