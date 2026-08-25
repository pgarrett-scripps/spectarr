from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Artifact,
    ArtifactRole,
    ArtifactState,
    Experiment,
    Project,
    Run,
    RunSample,
    Sample,
    SdrfDocument,
    SdrfRow,
)
from .storage import LocalArtifactStorage


SDRF_VERSION = "v1.1.0"
BASE_TEMPLATE = f"ms-proteomics {SDRF_VERSION}"
RESERVED_VALUES = {"not available", "not applicable", "anonymized", "pooled"}
REQUIRED_COLUMNS = [
    "source name",
    "characteristics[organism]",
    "characteristics[organism part]",
    "characteristics[disease]",
    "characteristics[biological replicate]",
    "assay name",
    "technology type",
    "comment[proteomics data acquisition method]",
    "comment[label]",
    "comment[instrument]",
    "comment[cleavage agent details]",
    "comment[fraction identifier]",
    "comment[technical replicate]",
    "comment[data file]",
    "comment[sdrf version]",
    "comment[sdrf template]",
]
COMMON_TEMPLATES = [
    {"name": "ms-proteomics", "version": SDRF_VERSION, "kind": "technology"},
    {"name": "human", "version": SDRF_VERSION, "kind": "sample"},
    {"name": "vertebrates", "version": SDRF_VERSION, "kind": "sample"},
    {"name": "invertebrates", "version": SDRF_VERSION, "kind": "sample"},
    {"name": "plants", "version": SDRF_VERSION, "kind": "sample"},
    {"name": "cell-lines", "version": SDRF_VERSION, "kind": "sample"},
    {"name": "clinical-metadata", "version": SDRF_VERSION, "kind": "sample"},
    {"name": "oncology-metadata", "version": SDRF_VERSION, "kind": "sample"},
    {"name": "dia-acquisition", "version": SDRF_VERSION, "kind": "experiment"},
    {"name": "single-cell", "version": SDRF_VERSION, "kind": "experiment"},
    {"name": "immunopeptidomics", "version": SDRF_VERSION, "kind": "experiment"},
    {"name": "crosslinking", "version": SDRF_VERSION, "kind": "experiment"},
    {"name": "metaproteomics", "version": SDRF_VERSION, "kind": "experiment"},
    {"name": "top-down-proteomics", "version": SDRF_VERSION, "kind": "experiment"},
]


def parse_sdrf(content: bytes) -> tuple[list[str], list[list[str]]]:
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "SDRF files are limited to 50 MiB")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "SDRF must be UTF-8 text") from error
    reader = csv.reader(io.StringIO(text, newline=""), delimiter="\t")
    records = [[cell.strip() for cell in record] for record in reader]
    records = [record for record in records if any(record)]
    if not records:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "SDRF is empty")
    columns = records[0]
    if not all(columns):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "SDRF column names cannot be empty")
    rows = records[1:]
    for index, row in enumerate(rows, start=2):
        if len(row) != len(columns):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"SDRF row {index} has {len(row)} cells but {len(columns)} columns",
            )
    return columns, rows


def serialize_sdrf(columns: list[str], rows: list[list[str]]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    writer.writerow(columns)
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def document_view(document: SdrfDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "project_id": document.project_id,
        "specification_version": document.specification_version,
        "templates": document.templates,
        "columns": document.columns,
        "rows": [
            {
                "id": row.id,
                "position": row.position,
                "values": row.values,
                "sample_id": row.sample_id,
                "run_id": row.run_id,
                "artifact_id": row.artifact_id,
            }
            for row in document.rows
        ],
        "status": document.status,
        "revision": document.revision,
        "source_filename": document.source_filename,
        "content_sha256": document.content_sha256,
        "validation_engine": document.validation_engine,
        "validation_report": document.validation_report,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
    }


def replace_document(
    session: Session,
    project: Project,
    columns: list[str],
    rows: list[list[str]],
    templates: list[str] | None = None,
    source_filename: str | None = None,
    synchronize: bool = True,
) -> SdrfDocument:
    document = project.sdrf_document
    if document is None:
        document = SdrfDocument(project=project)
        session.add(document)
        session.flush()
    else:
        document.rows.clear()
        session.flush()
        document.revision += 1
    document.columns = columns
    document.templates = templates or templates_from_table(columns, rows) or [BASE_TEMPLATE]
    document.specification_version = version_from_table(columns, rows) or SDRF_VERSION
    document.source_filename = source_filename
    document.status = "draft"
    document.validation_engine = None
    document.validation_report = {}
    encoded = serialize_sdrf(columns, rows)
    document.content_sha256 = hashlib.sha256(encoded).hexdigest()
    mapped = map_rows(session, project, columns, rows, synchronize=synchronize)
    document.rows = [
        SdrfRow(
            position=index,
            values=values,
            sample_id=links.get("sample_id"),
            run_id=links.get("run_id"),
            artifact_id=links.get("artifact_id"),
        )
        for index, (values, links) in enumerate(zip(rows, mapped, strict=True))
    ]
    session.commit()
    session.refresh(document)
    return document


def generate_document(session: Session, project: Project) -> SdrfDocument:
    runs = [run for experiment in project.experiments for run in experiment.runs]
    max_associated = max(
        (
            max(0, len([artifact for artifact in run.artifacts if is_ready_source(artifact)]) - 1)
            for run in runs
        ),
        default=0,
    )
    columns = list(REQUIRED_COLUMNS)
    for _ in range(max_associated):
        columns.append("comment[associated data file]")
    columns.append("comment[sdrf annotation tool]")
    rows: list[list[str]] = []
    for run in sorted(runs, key=lambda item: (item.experiment.name.casefold(), item.name.casefold(), item.id)):
        sources = sorted(
            [artifact for artifact in run.artifacts if is_ready_source(artifact)],
            key=lambda artifact: source_priority(artifact.original_filename),
        )
        primary = sources[0] if sources else None
        associations = run.sample_links
        if not associations and run.sample:
            associations = [
                RunSample(run=run, sample=run.sample, position=0, label="label free sample", role="analyte")
            ]
        if not associations:
            associations = [None]
        for association in associations:
            sample = association.sample if association else None
            values = {
                "source name": sample.name if sample else f"unassigned_{run.id[:8]}",
                "characteristics[organism]": sample_value(sample, "characteristics[organism]", "organism"),
                "characteristics[organism part]": sample_value(
                    sample, "characteristics[organism part]", "organism_part", "tissue"
                ),
                "characteristics[disease]": sample_value(sample, "characteristics[disease]", "disease"),
                "characteristics[biological replicate]": sample_value(
                    sample, "characteristics[biological replicate]", "biological_replicate", default="1"
                ),
                "assay name": run.name,
                "technology type": run_value(
                    run, "technology_type", default="proteomic profiling by mass spectrometry"
                ),
                "comment[proteomics data acquisition method]": run_value(
                    run, "comment[proteomics data acquisition method]", "acquisition_method"
                ),
                "comment[label]": association.label if association else "label free sample",
                "comment[instrument]": instrument_value(run),
                "comment[cleavage agent details]": sample_value(
                    sample, "comment[cleavage agent details]", "cleavage_agent"
                ),
                "comment[fraction identifier]": run_value(
                    run, "comment[fraction identifier]", "fraction_identifier", default="1"
                ),
                "comment[technical replicate]": run_value(
                    run, "comment[technical replicate]", "technical_replicate", default="1"
                ),
                "comment[data file]": submission_filename(primary) if primary else "not available",
                "comment[sdrf version]": SDRF_VERSION,
                "comment[sdrf template]": BASE_TEMPLATE,
                "comment[sdrf annotation tool]": "Spectarr v0.1.0",
            }
            row = [values.get(column, "not available") for column in REQUIRED_COLUMNS]
            associated_sources = sources[1:]
            row.extend(
                submission_filename(associated_sources[index])
                if index < len(associated_sources)
                else "not applicable"
                for index in range(max_associated)
            )
            row.append(values["comment[sdrf annotation tool]"])
            rows.append(row)
    return replace_document(
        session,
        project,
        columns,
        rows,
        templates=[BASE_TEMPLATE],
        source_filename=f"{safe_name(project.name)}.sdrf.tsv",
        synchronize=True,
    )


def validate_document(document: SdrfDocument, ontology: bool = False) -> dict[str, Any]:
    report = structural_validation(document.columns, [row.values for row in document.rows])
    official = official_validation(document, ontology)
    report["messages"].extend(official["messages"])
    report["engine"] = official["engine"]
    report["ontology"] = ontology
    report["error_count"] = sum(message["severity"] == "error" for message in report["messages"])
    report["warning_count"] = sum(message["severity"] == "warning" for message in report["messages"])
    report["valid"] = report["error_count"] == 0
    return report


def structural_validation(columns: list[str], rows: list[list[str]]) -> dict[str, Any]:
    messages: list[dict[str, Any]] = []
    normalized = [column.casefold().strip() for column in columns]
    counts = Counter(normalized)
    repeatable = {
        "comment[sdrf template]",
        "comment[associated data file]",
        "comment[associated file uri]",
    }
    for column, count in counts.items():
        if count > 1 and column not in repeatable:
            messages.append(message("error", "duplicate_column", f"Column '{column}' appears {count} times"))
    for required in REQUIRED_COLUMNS:
        if required not in normalized:
            messages.append(message("error", "missing_column", f"Required column '{required}' is missing"))
    for index, original in enumerate(columns):
        if original != normalized[index]:
            messages.append(
                message("warning", "noncanonical_header", f"Use lowercase canonical header '{normalized[index]}'", column=index)
            )
    required_positions = [index for index, column in enumerate(normalized) if column in REQUIRED_COLUMNS]
    source_position = first_position(normalized, "source name")
    assay_position = first_position(normalized, "assay name")
    label_position = first_position(normalized, "comment[label]")
    file_position = first_position(normalized, "comment[data file]")
    unique_rows: set[tuple[str, str, str]] = set()
    assay_files: dict[str, str] = {}
    for row_index, row in enumerate(rows):
        if len(row) != len(columns):
            messages.append(message("error", "row_width", "Row width does not match the header", row=row_index))
            continue
        for position in required_positions:
            if not row[position].strip():
                messages.append(
                    message(
                        "error",
                        "empty_required_cell",
                        f"Required value for '{columns[position]}' is empty",
                        row=row_index,
                        column=position,
                    )
                )
        if source_position is not None and assay_position is not None and label_position is not None:
            key = (row[source_position], row[assay_position], row[label_position])
            if key in unique_rows:
                messages.append(message("error", "duplicate_sample_assay_label", "Duplicate sample, assay, and label", row=row_index))
            unique_rows.add(key)
        if assay_position is not None and file_position is not None:
            assay = row[assay_position]
            filename = row[file_position]
            if assay in assay_files and assay_files[assay] != filename:
                messages.append(
                    message("error", "assay_file_conflict", "One assay name references multiple primary data files", row=row_index)
                )
            assay_files[assay] = filename
    return {"engine": "spectarr-structural", "messages": messages}


def official_validation(document: SdrfDocument, ontology: bool) -> dict[str, Any]:
    executable = shutil.which("parse_sdrf")
    if executable is None:
        return {
            "engine": "spectarr-structural",
            "messages": [
                message(
                    "warning",
                    "official_validator_unavailable",
                    "Official sdrf-pipelines validation is not installed in this runtime",
                )
            ],
        }
    with tempfile.NamedTemporaryFile(suffix=".sdrf.tsv") as temporary:
        temporary.write(serialize_sdrf(document.columns, [row.values for row in document.rows]))
        temporary.flush()
        command = [executable, "validate-sdrf", "--sdrf_file", temporary.name]
        for template in document.templates:
            command.extend(["--template", template.split()[0]])
        if not ontology:
            command.append("--skip-ontology")
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            return {
                "engine": "sdrf-pipelines",
                "messages": [message("error", "official_validator_timeout", "Official validation exceeded 180 seconds")],
            }
    output = "\n".join(part for part in [completed.stdout.strip(), completed.stderr.strip()] if part)
    messages: list[dict[str, Any]] = []
    for raw_line in output[-12000:].splitlines():
        line = raw_line.strip()
        if not line or line.casefold() == "there were validation errors.":
            continue
        if line.startswith("ERROR:"):
            messages.append(message("error", "sdrf_pipelines", line.removeprefix("ERROR:").strip()))
        elif line.startswith("WARNING:"):
            messages.append(message("warning", "sdrf_pipelines", line.removeprefix("WARNING:").strip()))
        else:
            messages.append(message("info", "sdrf_pipelines", line))
    if completed.returncode and not output:
        messages.append(message("error", "sdrf_pipelines", "Official validator failed without output"))
    elif completed.returncode and not any(item["severity"] == "error" for item in messages):
        messages.append(message("error", "sdrf_pipelines", "Official validator reported failure"))
    return {"engine": "sdrf-pipelines", "messages": messages}


def map_rows(
    session: Session,
    project: Project,
    columns: list[str],
    rows: list[list[str]],
    synchronize: bool,
) -> list[dict[str, str | None]]:
    normalized = [column.casefold().strip() for column in columns]
    source_position = first_position(normalized, "source name")
    assay_position = first_position(normalized, "assay name")
    file_position = first_position(normalized, "comment[data file]")
    label_position = first_position(normalized, "comment[label]")
    project_runs = [run for experiment in project.experiments for run in experiment.runs]
    runs_by_assay = unique_index(project_runs, lambda run: run.name)
    artifacts = [artifact for run in project_runs for artifact in run.artifacts if is_ready_source(artifact)]
    artifacts_by_name = unique_index(artifacts, lambda artifact: submission_filename(artifact))
    artifacts_by_original = unique_index(artifacts, lambda artifact: artifact.original_filename)
    samples = [sample for experiment in project.experiments for sample in experiment.samples]
    samples_by_name = unique_index(samples, lambda sample: sample.name)
    mapped_runs: set[str] = set()
    mapped: list[dict[str, str | None]] = []
    pending_links: list[tuple[Run, Sample, str, dict[str, str]]] = []
    for row in rows:
        assay_name = value_at(row, assay_position)
        filename = value_at(row, file_position)
        run = runs_by_assay.get(assay_name)
        artifact = artifacts_by_name.get(filename) or artifacts_by_original.get(filename)
        if run is None and artifact is not None:
            run = artifact.run
        source_name = value_at(row, source_position)
        sample = samples_by_name.get(source_name)
        if sample is None and run is not None and source_name and source_name not in RESERVED_VALUES:
            sample_metadata = {
                normalized[index]: value
                for index, value in enumerate(row)
                if normalized[index].startswith("characteristics[")
            }
            sample = Sample(
                experiment=run.experiment,
                name=source_name,
                metadata_json=sample_metadata,
            )
            session.add(sample)
            session.flush()
            samples_by_name[source_name] = sample
        if run is not None:
            mapped_runs.add(run.id)
            comments = {
                normalized[index]: value
                for index, value in enumerate(row)
                if normalized[index].startswith("comment[") and value
            }
            run.metadata_json = {**run.metadata_json, "sdrf": comments}
        if run is not None and sample is not None:
            label = value_at(row, label_position) or "label free sample"
            link_metadata = {
                normalized[index]: value
                for index, value in enumerate(row)
                if normalized[index].startswith("factor value[") or normalized[index] == "characteristics[pooled sample]"
            }
            pending_links.append((run, sample, label, link_metadata))
        mapped.append(
            {
                "sample_id": sample.id if sample else None,
                "run_id": run.id if run else None,
                "artifact_id": artifact.id if artifact else None,
            }
        )
    if synchronize and mapped_runs:
        affected_runs = [run for run in project_runs if run.id in mapped_runs]
        for run in affected_runs:
            run.sample_links.clear()
            run.sample = None
        session.flush()
        positions: defaultdict[str, int] = defaultdict(int)
        seen: set[tuple[str, str, str]] = set()
        for run, sample, label, metadata in pending_links:
            key = (run.id, sample.id, label)
            if key in seen:
                continue
            seen.add(key)
            session.add(
                RunSample(
                    run=run,
                    sample=sample,
                    label=label,
                    position=positions[run.id],
                    metadata_json=metadata,
                )
            )
            positions[run.id] += 1
            if positions[run.id] == 1:
                run.sample = sample
    return mapped


def submission_preview(project: Project) -> dict[str, Any]:
    artifacts = [
        artifact
        for experiment in project.experiments
        for run in experiment.runs
        for artifact in run.artifacts
        if artifact.state == ArtifactState.READY
        and artifact.role in {ArtifactRole.SOURCE, ArtifactRole.DERIVED}
    ]
    sources = [artifact for artifact in artifacts if artifact.role == ArtifactRole.SOURCE]
    derivatives = [artifact for artifact in artifacts if artifact.role == ArtifactRole.DERIVED]
    document = project.sdrf_document
    return {
        "project_id": project.id,
        "source_count": len(sources),
        "derivative_count": len(derivatives),
        "total_bytes": sum(artifact.byte_size for artifact in artifacts),
        "sdrf_status": document.status if document else "missing",
        "sdrf_revision": document.revision if document else None,
        "mapped_rows": sum(bool(row.artifact_id) for row in document.rows) if document else 0,
        "unmapped_rows": sum(not row.artifact_id for row in document.rows) if document else 0,
        "ready": bool(document and document.status == "valid" and sources),
    }


def build_submission_zip(project: Project, storage: LocalArtifactStorage) -> Path:
    document = project.sdrf_document
    if document is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Generate or import SDRF metadata first")
    if document.status != "valid":
        raise HTTPException(status.HTTP_409_CONFLICT, "SDRF metadata must pass validation before export")
    handle = tempfile.NamedTemporaryFile(prefix="spectarr-submission-", suffix=".zip", delete=False)
    path = Path(handle.name)
    handle.close()
    artifacts = [
        artifact
        for experiment in project.experiments
        for run in experiment.runs
        for artifact in run.artifacts
        if artifact.state == ArtifactState.READY
        and artifact.role in {ArtifactRole.SOURCE, ArtifactRole.DERIVED}
    ]
    manifest: list[dict[str, Any]] = []
    checksums: list[str] = []
    used_names: set[str] = set()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        sdrf_name = document.source_filename or f"{safe_name(project.name)}.sdrf.tsv"
        archive.writestr(sdrf_name, serialize_sdrf(document.columns, [row.values for row in document.rows]))
        for artifact in artifacts:
            filename = submission_filename(artifact)
            if filename in used_names:
                filename = f"{artifact.run_id[:8]}__{filename}"
            used_names.add(filename)
            source = storage.resolve(artifact.storage_key)
            if source.is_dir():
                payload = source / "payload" / artifact.original_filename
                for child in sorted(payload.rglob("*")):
                    if child.is_file():
                        relative = child.relative_to(payload).as_posix()
                        archive_name = f"files/{filename}/{relative}"
                        archive.write(child, archive_name)
                        file_record = next(
                            (
                                item
                                for item in (artifact.bundle_manifest or {}).get("files", [])
                                if item.get("path") == relative
                            ),
                            None,
                        )
                        if file_record:
                            checksums.append(f"{file_record['sha256']}  {archive_name}")
            else:
                archive.write(source, f"files/{filename}")
                checksums.append(f"{artifact.sha256}  files/{filename}")
            manifest.append(
                {
                    "artifact_id": artifact.id,
                    "run_id": artifact.run_id,
                    "filename": filename,
                    "original_filename": artifact.original_filename,
                    "role": enum_value(artifact.role),
                    "format": enum_value(artifact.format),
                    "sha256": artifact.sha256,
                    "byte_size": artifact.byte_size,
                }
            )
        archive.writestr("checksums.sha256", "\n".join(checksums) + "\n")
        archive.writestr(
            "spectarr-submission.json",
            json.dumps(
                {
                    "schema": "spectarr.repository-submission/v1",
                    "project": {
                        "id": project.id,
                        "name": project.name,
                        "description": project.description,
                        "metadata": project.metadata_json,
                    },
                    "sdrf": {
                        "filename": sdrf_name,
                        "revision": document.revision,
                        "sha256": document.content_sha256,
                        "templates": document.templates,
                    },
                    "files": manifest,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    return path


def is_ready_source(artifact: Artifact) -> bool:
    return artifact.role == ArtifactRole.SOURCE and artifact.state == ArtifactState.READY


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def source_priority(filename: str) -> tuple[int, str]:
    lower = filename.casefold()
    sidecar = lower.endswith((".wiff.scan", ".scan", ".idx"))
    return (1 if sidecar else 0, lower)


def submission_filename(artifact: Artifact | None) -> str:
    if artifact is None:
        return "not available"
    return Path(artifact.library_path).name if artifact.library_path else Path(artifact.original_filename).name


def sample_value(sample: Sample | None, *keys: str, default: str = "not available") -> str:
    if sample is None:
        return default
    for key in keys:
        value = sample.metadata_json.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def run_value(run: Run, *keys: str, default: str = "not available") -> str:
    sdrf = run.metadata_json.get("sdrf", {})
    for key in keys:
        value = sdrf.get(key, run.metadata_json.get(key))
        if value not in (None, ""):
            return str(value)
    return default


def instrument_value(run: Run) -> str:
    explicit = run_value(run, "comment[instrument]", "instrument_cv", default="")
    if explicit:
        return explicit
    if run.instrument:
        return run.instrument.model or run.instrument.name
    return "not available"


def templates_from_table(columns: list[str], rows: list[list[str]]) -> list[str]:
    positions = [
        index for index, column in enumerate(columns) if column.casefold().strip() == "comment[sdrf template]"
    ]
    values: list[str] = []
    for row in rows:
        for position in positions:
            value = row[position].strip()
            if value and value not in values:
                values.append(value)
    return values


def version_from_table(columns: list[str], rows: list[list[str]]) -> str | None:
    position = first_position([column.casefold().strip() for column in columns], "comment[sdrf version]")
    return value_at(rows[0], position) if rows else None


def first_position(columns: list[str], name: str) -> int | None:
    try:
        return columns.index(name)
    except ValueError:
        return None


def value_at(row: list[str], position: int | None) -> str:
    return row[position].strip() if position is not None and position < len(row) else ""


def unique_index(values: list[Any], key) -> dict[str, Any]:
    grouped: defaultdict[str, list[Any]] = defaultdict(list)
    for value in values:
        grouped[str(key(value))].append(value)
    return {name: items[0] for name, items in grouped.items() if name and len(items) == 1}


def message(
    severity: str,
    code: str,
    text: str,
    row: int | None = None,
    column: int | None = None,
) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": text, "row": row, "column": column}


def safe_name(value: str) -> str:
    normalized = "".join(character.lower() if character.isalnum() else "-" for character in value)
    return "-".join(part for part in normalized.split("-") if part) or "spectarr-project"
