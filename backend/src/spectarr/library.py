from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Artifact, ArtifactRole, ArtifactState, Project, Run
from .storage import LocalArtifactStorage


LIBRARY_SCHEMA = "spectarr.library/v2"
INVALID_FILENAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
TOKEN = re.compile(r"\{([a-z_]+)(?::(\d+))?\}")
WINDOWS_RESERVED = {
    "aux",
    "con",
    "nul",
    "prn",
    *{f"com{number}" for number in range(1, 10)},
    *{f"lpt{number}" for number in range(1, 10)},
}
FORMAT_DIRECTORIES = {
    "raw": "raw",
    "wiff": "raw",
    "vendor_directory": "raw",
    "mzml": "mzml",
    "mzxml": "mzxml",
    "mgf": "mgf",
    "ms2": "ms2",
    "parquet": "parquet",
}
SUFFIX_DIRECTORIES = {
    ".mzml.gz": "mzml",
    ".mzml": "mzml",
    ".mzxml": "mzxml",
    ".mgf": "mgf",
    ".ms2": "ms2",
    ".raw": "raw",
    ".wiff": "raw",
    ".d": "raw",
}
ROLE_FALLBACK_DIRECTORIES = {
    ArtifactRole.PREVIEW: "previews",
    ArtifactRole.ANALYSIS_RESULT: "analysis",
    ArtifactRole.ATTACHMENT: "attachments",
    "preview": "previews",
    "analysis_result": "analysis",
    "attachment": "attachments",
}


def short_id(value: str) -> str:
    return value.replace("-", "")[:8]


def safe_component(value: str, *, slug: bool = False, maximum: int = 240) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = INVALID_FILENAME.sub("_", normalized)
    normalized = re.sub(r"\s+", "-" if slug else " ", normalized)
    if slug:
        normalized = re.sub(r"[^\w.-]+", "-", normalized, flags=re.UNICODE)
        normalized = re.sub(r"-{2,}", "-", normalized).lower()
    normalized = normalized.strip(" .-")[:maximum].rstrip(" .")
    if not normalized or normalized in {".", ".."}:
        normalized = "unnamed"
    if normalized.casefold() in WINDOWS_RESERVED:
        normalized = f"_{normalized}"
    return normalized


def original_extension(filename: str) -> str:
    if filename.casefold().endswith(".mzml.gz"):
        return filename[-8:]
    return Path(filename).suffix


def original_stem(filename: str) -> str:
    extension = original_extension(filename)
    return filename[: -len(extension)] if extension else filename


def filename_with_artifact_id(filename: str, artifact_id: str) -> str:
    extension = original_extension(filename)
    stem = filename[: -len(extension)] if extension else filename
    return f"{stem}__{short_id(artifact_id)}{extension}"


def render_template(template: str, values: dict[str, str]) -> str:
    def replacement(match: re.Match[str]) -> str:
        name, length = match.groups()
        if name not in values:
            raise ValueError(f"Unknown library naming token: {name}")
        value = values[name]
        return value[: int(length)] if length else value

    rendered = TOKEN.sub(replacement, template)
    if "{" in rendered or "}" in rendered:
        raise ValueError("Invalid library naming template")
    return rendered


class LibraryMaterializer:
    def __init__(self, storage: LocalArtifactStorage):
        self.storage = storage

    def project_directory(self, project: Project) -> Path:
        rendered = render_template(
            self.storage.project_template,
            {
                "project_name": project.name,
                "project_id": project.id.replace("-", ""),
            },
        )
        return Path(safe_component(rendered, slug=True))

    def format_directory(self, artifact: Artifact) -> str:
        normalized_format = str(artifact.format).casefold()
        if normalized_format in FORMAT_DIRECTORIES:
            return FORMAT_DIRECTORIES[normalized_format]
        normalized_filename = artifact.original_filename.casefold()
        for suffix, directory in SUFFIX_DIRECTORIES.items():
            if normalized_filename.endswith(suffix):
                return directory
        return ROLE_FALLBACK_DIRECTORIES.get(
            artifact.role,
            safe_component(normalized_format or "other", slug=True),
        )

    def artifact_filename(self, artifact: Artifact) -> str:
        run = artifact.run
        acquired = run.acquired_at or run.created_at
        linked_samples = [link.sample for link in run.sample_links]
        primary_sample = linked_samples[0] if linked_samples else run.sample
        values = {
            "project_name": run.experiment.project.name,
            "project_id": run.experiment.project.id.replace("-", ""),
            "experiment_name": run.experiment.name,
            "experiment_id": run.experiment.id.replace("-", ""),
            "sample_name": primary_sample.name if primary_sample else "unassigned",
            "sample_id": primary_sample.id.replace("-", "") if primary_sample else "unassigned",
            "run_name": run.name,
            "run_id": run.id.replace("-", ""),
            "artifact_id": artifact.id.replace("-", ""),
            "instrument_name": run.instrument.name if run.instrument else "unknown-instrument",
            "acquired_date": acquired.date().isoformat(),
            "original_filename": artifact.original_filename,
            "original_stem": original_stem(artifact.original_filename),
            "extension": original_extension(artifact.original_filename),
            "format": str(artifact.format),
            "role": enum_value(artifact.role),
            "recipe_name": artifact.recipe.name if artifact.recipe else "source",
        }
        return safe_component(render_template(self.storage.filename_template, values))

    def run_manifest_key(self, run: Run) -> str:
        project_directory = self.project_directory(run.experiment.project)
        return (project_directory / ".spectarr" / "runs" / f"{run.id}.json").as_posix()

    def project_manifest_key(self, project: Project) -> str:
        return (self.project_directory(project) / "spectarr-project.json").as_posix()

    def materialize_artifact(self, artifact: Artifact) -> str:
        project = artifact.run.experiment.project
        if artifact.library_path:
            library_key = artifact.library_path
        else:
            candidate = (
                self.project_directory(project)
                / self.format_directory(artifact)
                / self.artifact_filename(artifact)
            )
            destination = self.storage.resolve_library(candidate.as_posix())
            if destination.exists():
                candidate = candidate.with_name(filename_with_artifact_id(candidate.name, artifact.id))
            library_key = candidate.as_posix()
        mode = self.storage.materialize(
            artifact.storage_key,
            library_key,
            artifact.original_filename,
        )
        artifact.library_path = library_key
        artifact.materialization_mode = mode
        self.write_run_manifest(artifact.run)
        self.write_project_manifest(project)
        return library_key

    def artifact_manifest(self, artifact: Artifact, project_directory: Path) -> dict:
        recipe = artifact.recipe
        return {
            "id": artifact.id,
            "run_id": artifact.run_id,
            "role": enum_value(artifact.role),
            "format": artifact.format,
            "original_filename": artifact.original_filename,
            "path": Path(artifact.library_path).relative_to(project_directory).as_posix(),
            "library_path": artifact.library_path,
            "byte_size": artifact.byte_size,
            "sha256": artifact.sha256,
            "is_directory": artifact.bundle_manifest is not None,
            "parent_artifact_id": artifact.parent_artifact_id,
            "materialization_mode": artifact.materialization_mode,
            "download_url": f"/api/v1/artifacts/{artifact.id}/download",
            "recipe": (
                {
                    "id": recipe.id,
                    "name": recipe.name,
                    "converter": recipe.converter,
                    "converter_version": recipe.converter_version,
                    "parameters": recipe.parameters,
                }
                if recipe
                else None
            ),
            "created_at": isoformat(artifact.created_at),
        }

    def write_run_manifest(self, run: Run) -> Path:
        project = run.experiment.project
        project_directory = self.project_directory(project)
        artifacts = [
            self.artifact_manifest(artifact, project_directory)
            for artifact in sorted(run.artifacts, key=lambda item: (isoformat(item.created_at) or "", item.id))
            if artifact.library_path
        ]
        sample_links = [
            {
                "id": link.sample.id,
                "name": link.sample.name,
                "label": link.label,
                "role": link.role,
                "position": link.position,
                "metadata": link.sample.metadata_json,
                "link_metadata": link.metadata_json,
            }
            for link in run.sample_links
        ]
        if not sample_links and run.sample:
            sample_links = [
                {
                    "id": run.sample.id,
                    "name": run.sample.name,
                    "label": "label free sample",
                    "role": "analyte",
                    "position": 0,
                    "metadata": run.sample.metadata_json,
                    "link_metadata": {},
                }
            ]
        payload = {
            "schema": LIBRARY_SCHEMA,
            "generated_at": isoformat(datetime.now(timezone.utc)),
            "project": {"id": project.id, "name": project.name},
            "experiment": {
                "id": run.experiment.id,
                "name": run.experiment.name,
                "description": run.experiment.description,
            },
            "run": {
                "id": run.id,
                "name": run.name,
                "source_class": enum_value(run.source_class),
                "acquired_at": isoformat(run.acquired_at),
                "created_at": isoformat(run.created_at),
                "metadata": run.metadata_json,
            },
            "sample": (
                sample_links[0]
                if sample_links
                else None
            ),
            "samples": sample_links,
            "instrument": (
                {
                    "id": run.instrument.id,
                    "name": run.instrument.name,
                    "vendor": run.instrument.vendor,
                    "model": run.instrument.model,
                    "serial_number": run.instrument.serial_number,
                }
                if run.instrument
                else None
            ),
            "artifacts": artifacts,
        }
        return self.storage.write_library_json(self.run_manifest_key(run), payload)

    def write_project_manifest(self, project: Project) -> Path:
        project_directory = self.project_directory(project)
        experiments = sorted(project.experiments, key=lambda item: (item.name.casefold(), item.id))
        runs = sorted(
            (run for experiment in experiments for run in experiment.runs),
            key=lambda item: (isoformat(item.created_at) or "", item.id),
        )
        format_directories = sorted(
            {
                self.format_directory(artifact)
                for run in runs
                for artifact in run.artifacts
                if artifact.library_path
            }
        )
        payload = {
            "schema": LIBRARY_SCHEMA,
            "generated_at": isoformat(datetime.now(timezone.utc)),
            "project": {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "path": project_directory.as_posix(),
                "metadata": project.metadata_json,
            },
            "sdrf": (
                {
                    "status": project.sdrf_document.status,
                    "revision": project.sdrf_document.revision,
                    "filename": project.sdrf_document.source_filename,
                    "sha256": project.sdrf_document.content_sha256,
                }
                if project.sdrf_document
                else None
            ),
            "naming": {
                "project_template": self.storage.project_template,
                "filename_template": self.storage.filename_template,
            },
            "format_directories": format_directories,
            "experiments": [
                {"id": experiment.id, "name": experiment.name, "description": experiment.description}
                for experiment in experiments
            ],
            "runs": [
                {
                    "id": run.id,
                    "name": run.name,
                    "experiment_id": run.experiment_id,
                    "sample_id": run.sample_id,
                    "sample_name": run.sample.name if run.sample else None,
                    "samples": [
                        {
                            "id": link.sample.id,
                            "name": link.sample.name,
                            "label": link.label,
                            "role": link.role,
                            "position": link.position,
                        }
                        for link in run.sample_links
                    ],
                    "instrument_id": run.instrument_id,
                    "acquired_at": isoformat(run.acquired_at),
                    "manifest": Path(self.run_manifest_key(run)).relative_to(project_directory).as_posix(),
                    "artifacts": [
                        self.artifact_manifest(artifact, project_directory)
                        for artifact in sorted(run.artifacts, key=lambda item: item.id)
                        if artifact.library_path
                    ],
                }
                for run in runs
            ],
        }
        return self.storage.write_library_json(self.project_manifest_key(project), payload)

    def write_catalog(self, session: Session) -> Path:
        projects = list(session.scalars(select(Project).order_by(Project.name, Project.id)))
        payload = {
            "schema": LIBRARY_SCHEMA,
            "generated_at": isoformat(datetime.now(timezone.utc)),
            "description": "Project-first Spectarr library with search-ready format directories.",
            "projects": [
                {
                    "id": project.id,
                    "name": project.name,
                    "path": self.project_directory(project).as_posix(),
                    "manifest": self.project_manifest_key(project),
                    "run_count": sum(len(experiment.runs) for experiment in project.experiments),
                }
                for project in projects
            ],
        }
        return self.storage.write_library_json("spectarr-library.json", payload)

    def rebuild(self, session: Session) -> dict[str, int]:
        artifacts = list(
            session.scalars(
                select(Artifact)
                .where(Artifact.state == ArtifactState.READY)
                .order_by(Artifact.created_at, Artifact.id)
            )
        )
        self.storage.clear_library()
        for artifact in artifacts:
            artifact.library_path = None
            artifact.materialization_mode = None
        session.flush()
        copied = 0
        linked = 0
        for artifact in artifacts:
            self.materialize_artifact(artifact)
            if artifact.materialization_mode == "copy":
                copied += 1
            else:
                linked += 1
        for project in session.scalars(select(Project).order_by(Project.name, Project.id)):
            self.write_project_manifest(project)
        self.write_catalog(session)
        return {"artifacts": len(artifacts), "hardlinked": linked, "copied": copied}


def enum_value(value: object) -> str:
    return str(getattr(value, "value", value))


def isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
