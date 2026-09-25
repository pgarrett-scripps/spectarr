"""Report artifact access without confusing recorded identity with live integrity."""

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from .models import Artifact, ArtifactState
from .storage import LocalArtifactStorage


class ArtifactAccessRead(BaseModel):
    schema_version: Literal[1] = 1
    artifact_id: str
    run_id: str
    project_id: str
    filename: str
    format: str
    role: str
    state: str
    byte_size: int
    sha256: str
    is_directory: bool
    availability: Literal["available", "unmaterialized", "missing", "purged", "not_ready"]
    checked_at: datetime
    integrity: Literal["checksum_recorded_not_reverified"]
    path_scope: Literal["api_server"]
    library_root: str
    library_relative_path: str | None
    server_path: str | None = Field(description="Read-only library path on the API server, possibly inside a container")
    download_url: str | None = Field(description="Authenticated path relative to the API origin, absent for directory bundles")
    parent_artifact_id: str | None
    recipe_id: str | None
    recipe_fingerprint: str | None
    usage_note: str


def artifact_access_view(artifact: Artifact, storage: LocalArtifactStorage) -> ArtifactAccessRead:
    is_directory = artifact.bundle_manifest is not None
    ready = artifact.state == ArtifactState.READY
    object_path = storage.resolve(artifact.storage_key)
    library_path = storage.resolve_library(artifact.library_path) if artifact.library_path else None
    object_exists = object_path.is_dir() if is_directory else object_path.is_file()
    library_exists = bool(library_path and (library_path.is_dir() if is_directory else library_path.is_file()))
    if artifact.state == ArtifactState.MISSING:
        availability = "purged" if artifact.metadata_json.get("purged_at") else "missing"
    elif not ready:
        availability = "not_ready"
    elif not object_exists:
        availability = "missing"
    elif not library_exists:
        availability = "unmaterialized"
    else:
        availability = "available"
    return ArtifactAccessRead(**{
        "artifact_id": artifact.id,
        "run_id": artifact.run_id,
        "project_id": artifact.run.experiment.project_id,
        "filename": artifact.original_filename,
        "format": artifact.format,
        "role": artifact.role,
        "state": artifact.state,
        "byte_size": artifact.byte_size,
        "sha256": artifact.sha256,
        "is_directory": is_directory,
        "availability": availability,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "integrity": "checksum_recorded_not_reverified",
        "path_scope": "api_server",
        "library_root": str(storage.library),
        "library_relative_path": artifact.library_path,
        "server_path": str(library_path) if ready and library_exists else None,
        "download_url": (
            f"/api/v1/artifacts/{artifact.id}/download"
            if ready and object_exists and not is_directory else None
        ),
        "parent_artifact_id": artifact.parent_artifact_id,
        "recipe_id": artifact.recipe_id,
        "recipe_fingerprint": artifact.recipe_fingerprint,
        "usage_note": (
            "Paths refer to the API server filesystem, which may be inside a container. "
            "Map the library root to your local mount before opening a server path. "
            "Download URLs are relative to the API origin and require the same authentication. "
            "Directory bundles must be accessed as a whole through the managed library. "
            "Treat library files as read-only and write analysis outputs elsewhere. "
            "Availability checks existence and type only, not content integrity."
        ),
    })
