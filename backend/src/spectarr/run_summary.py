"""Choose scientific observations by provenance, independently of job order."""
from __future__ import annotations

from datetime import datetime, timezone

from .models import ArtifactRole, ExtractionResult, Run


METRIC_KEYS = {"spectra_count", "spectrum_count", "ms2_count", "duration_minutes"}


def timestamp(value: datetime) -> datetime:
    # SQLite reloads naive UTC values while a just-flushed row retains tzinfo.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def normalized_run_metadata(payload: dict) -> dict:
    summary = payload.get("qc_summary")
    summary = summary if isinstance(summary, dict) else {}
    values = {**payload, **summary}
    levels = summary.get("spectra_by_ms_level", {})
    normalized = {
        "spectra_count": values.get("spectrum_count", values.get("spectra_count")),
        "ms2_count": values.get("ms2_count", levels.get("2", levels.get(2)) if isinstance(levels, dict) else None),
        "duration_minutes": values.get("duration_minutes"),
    }
    seconds = summary.get("acquisition_duration_seconds")
    if normalized["duration_minutes"] is None and isinstance(seconds, (int, float)):
        normalized["duration_minutes"] = seconds / 60
    return {key: value for key, value in normalized.items() if value is not None}


def select_run_extraction(run: Run) -> tuple[ExtractionResult | None, str]:
    sources = {artifact.id for artifact in run.artifacts if artifact.role == ArtifactRole.SOURCE}
    candidates = [
        result for artifact in run.artifacts for result in artifact.extraction_results
        if result.result_type == "metadata" and (
            normalized_run_metadata(result.payload)
            or isinstance(result.payload.get("qc_summary"), dict) and result.payload["qc_summary"]
        )
    ]
    # Prefer the earliest source with observations. A later derivative cannot
    # replace it, even if its extraction finished more recently.
    source_results = [result for result in candidates if result.artifact_id in sources]
    if source_results:
        source_id = min(
            (result.artifact for result in source_results),
            key=lambda artifact: (timestamp(artifact.created_at), artifact.id),
        ).id
        return max(
            (result for result in source_results if result.artifact_id == source_id),
            key=lambda result: (timestamp(result.updated_at), result.id),
        ), "source"
    fallbacks = [
        result for result in candidates
        if result.artifact.role == ArtifactRole.DERIVED
        and result.artifact.parent_artifact_id in sources
        and result.artifact.format.casefold() in {"mzml", "mzxml"}
    ]
    if fallbacks:
        return max(fallbacks, key=lambda result: (timestamp(result.updated_at), result.id)), "linked_open_format_fallback"
    return None, "no_eligible_extraction"


def summary_basis(result: ExtractionResult | None, reason: str) -> dict:
    return {
        "policy": "source_then_linked_open_format/v1",
        "reason": reason,
        "artifact_id": result.artifact_id if result else None,
        "filename": result.artifact.original_filename if result else None,
        "format": result.artifact.format if result else None,
        "role": result.artifact.role if result else None,
    }


def run_metadata(run: Run, result: ExtractionResult | None) -> dict:
    # Earlier versions cached the last derivative's metrics on the run. Never
    # reuse those unscoped values, including when no eligible extraction exists.
    metadata = {key: value for key, value in (run.metadata_json or {}).items() if key not in METRIC_KEYS}
    return {**metadata, **(normalized_run_metadata(result.payload) if result else {})}
