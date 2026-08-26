from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def new_id() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ArtifactRole(str, enum.Enum):
    SOURCE = "source"
    DERIVED = "derived"
    PREVIEW = "preview"
    ANALYSIS_RESULT = "analysis_result"
    ATTACHMENT = "attachment"


class ArtifactState(str, enum.Enum):
    STAGING = "staging"
    VALIDATING = "validating"
    READY = "ready"
    FAILED = "failed"
    QUARANTINED = "quarantined"
    MISSING = "missing"


class SourceClass(str, enum.Enum):
    VENDOR = "vendor"
    OPEN = "open"
    SPECTRUM_LIST = "spectrum_list"
    UNKNOWN = "unknown"


class JobKind(str, enum.Enum):
    INGEST = "ingest"
    CONVERT = "convert"
    INDEX = "index"
    VERIFY = "verify"
    EXTRACT_METADATA = "extract_metadata"
    PREVIEW = "preview"


class JobState(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"
    SERVICE = "service"


class TokenKind(str, enum.Enum):
    API = "api"
    SESSION = "session"


class AutomationScope(str, enum.Enum):
    GLOBAL = "global"
    PROJECT = "project"
    INSTRUMENT = "instrument"


class UploadState(str, enum.Enum):
    OPEN = "open"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    system_key: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, default=dict, server_default="{}", nullable=False
    )

    experiments: Mapped[list[Experiment]] = relationship(back_populates="project", cascade="all, delete-orphan")
    memberships: Mapped[list[ProjectMembership]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    automation_rules: Mapped[list[AutomationRule]] = relationship(back_populates="project")
    sdrf_document: Mapped[SdrfDocument | None] = relationship(
        back_populates="project", cascade="all, delete-orphan", uselist=False
    )


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_experiment_project_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    intake_agent_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)

    project: Mapped[Project] = relationship(back_populates="experiments")
    samples: Mapped[list[Sample]] = relationship(back_populates="experiment", cascade="all, delete-orphan")
    runs: Mapped[list[Run]] = relationship(back_populates="experiment")


class Sample(TimestampMixin, Base):
    __tablename__ = "samples"
    __table_args__ = (UniqueConstraint("experiment_id", "name", name="uq_sample_experiment_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    experiment: Mapped[Experiment] = relationship(back_populates="samples")
    runs: Mapped[list[Run]] = relationship(back_populates="sample")
    run_links: Mapped[list[RunSample]] = relationship(back_populates="sample", cascade="all, delete-orphan")


class Instrument(TimestampMixin, Base):
    __tablename__ = "instruments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(255))
    model: Mapped[str | None] = mapped_column(String(255))
    serial_number: Mapped[str | None] = mapped_column(String(255), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    runs: Mapped[list[Run]] = relationship(back_populates="instrument")
    automation_rules: Mapped[list[AutomationRule]] = relationship(back_populates="instrument")


class Run(TimestampMixin, Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_name_created_at", "name", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id", ondelete="RESTRICT"), index=True)
    sample_id: Mapped[str | None] = mapped_column(ForeignKey("samples.id", ondelete="SET NULL"), index=True)
    instrument_id: Mapped[str | None] = mapped_column(ForeignKey("instruments.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_class: Mapped[SourceClass] = mapped_column(String(32), default=SourceClass.UNKNOWN, nullable=False)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    assignment_status: Mapped[str] = mapped_column(
        String(32), default="assigned", server_default="assigned", nullable=False, index=True
    )

    experiment: Mapped[Experiment] = relationship(back_populates="runs")
    sample: Mapped[Sample | None] = relationship(back_populates="runs")
    sample_links: Mapped[list[RunSample]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunSample.position"
    )
    samples: Mapped[list[Sample]] = relationship(
        secondary="run_samples", viewonly=True, overlaps="run_links,sample,run"
    )
    instrument: Mapped[Instrument | None] = relationship(back_populates="runs")
    artifacts: Mapped[list[Artifact]] = relationship(back_populates="run", cascade="all, delete-orphan")
    annotations: Mapped[list[RunAnnotation]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RunSample(TimestampMixin, Base):
    __tablename__ = "run_samples"
    __table_args__ = (
        UniqueConstraint("run_id", "sample_id", "label", name="uq_run_sample_label"),
        Index("ix_run_samples_run_position", "run_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    sample_id: Mapped[str] = mapped_column(ForeignKey("samples.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(default=0, nullable=False)
    label: Mapped[str] = mapped_column(String(255), default="label free sample", nullable=False)
    role: Mapped[str] = mapped_column(String(64), default="analyte", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    run: Mapped[Run] = relationship(back_populates="sample_links", overlaps="samples")
    sample: Mapped[Sample] = relationship(back_populates="run_links", overlaps="samples")


class SdrfDocument(TimestampMixin, Base):
    __tablename__ = "sdrf_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), unique=True, index=True
    )
    specification_version: Mapped[str] = mapped_column(String(32), default="v1.1.0", nullable=False)
    templates: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    columns: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False, index=True)
    revision: Mapped[int] = mapped_column(default=1, nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(1024))
    content_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    validation_engine: Mapped[str | None] = mapped_column(String(255))
    validation_report: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    project: Mapped[Project] = relationship(back_populates="sdrf_document")
    rows: Mapped[list[SdrfRow]] = relationship(
        back_populates="document", cascade="all, delete-orphan", order_by="SdrfRow.position"
    )


class SdrfRow(TimestampMixin, Base):
    __tablename__ = "sdrf_rows"
    __table_args__ = (UniqueConstraint("document_id", "position", name="uq_sdrf_document_position"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("sdrf_documents.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(nullable=False)
    values: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    sample_id: Mapped[str | None] = mapped_column(ForeignKey("samples.id", ondelete="SET NULL"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"), index=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), index=True)

    document: Mapped[SdrfDocument] = relationship(back_populates="rows")
    sample: Mapped[Sample | None] = relationship()
    run: Mapped[Run | None] = relationship()
    artifact: Mapped[Artifact | None] = relationship()


class RunAnnotation(TimestampMixin, Base):
    __tablename__ = "run_annotations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    author: Mapped[str | None] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    run: Mapped[Run] = relationship(back_populates="annotations")


class ConversionRecipe(TimestampMixin, Base):
    __tablename__ = "conversion_recipes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    converter: Mapped[str] = mapped_column(String(255), default="msconvert", nullable=False)
    converter_version: Mapped[str | None] = mapped_column(String(100))
    output_format: Mapped[str] = mapped_column(String(32), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    revision: Mapped[int] = mapped_column(default=1, server_default="1", nullable=False)
    system: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    artifacts: Mapped[list[Artifact]] = relationship(back_populates="recipe")
    jobs: Mapped[list[Job]] = relationship(back_populates="recipe")
    batch_items: Mapped[list[ProcessingBatchItem]] = relationship(back_populates="recipe")


class Artifact(TimestampMixin, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        Index("ix_artifacts_run_role", "run_id", "role"),
        Index("ix_artifacts_sha256", "sha256"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    parent_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), index=True)
    recipe_id: Mapped[str | None] = mapped_column(ForeignKey("conversion_recipes.id", ondelete="SET NULL"), index=True)
    role: Mapped[ArtifactRole] = mapped_column(String(32), nullable=False)
    state: Mapped[ArtifactState] = mapped_column(String(32), default=ArtifactState.READY, nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    library_path: Mapped[str | None] = mapped_column(String(2048), unique=True, index=True)
    materialization_mode: Mapped[str | None] = mapped_column(String(32))
    byte_size: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    recipe_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    immutable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    run: Mapped[Run] = relationship(back_populates="artifacts")
    parent: Mapped[Artifact | None] = relationship(remote_side="Artifact.id")
    recipe: Mapped[ConversionRecipe | None] = relationship(back_populates="artifacts")
    jobs_as_input: Mapped[list[Job]] = relationship(
        back_populates="input_artifact", foreign_keys="Job.input_artifact_id"
    )
    extraction_results: Mapped[list[ExtractionResult]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )
    spectrum_catalogs: Mapped[list[SpectrumCatalog]] = relationship(
        back_populates="artifact", cascade="all, delete-orphan"
    )


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_state_created", "state", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[JobKind] = mapped_column(String(32), nullable=False)
    state: Mapped[JobState] = mapped_column(String(32), default=JobState.QUEUED, nullable=False)
    input_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), index=True)
    output_artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), index=True)
    recipe_id: Mapped[str | None] = mapped_column(ForeignKey("conversion_recipes.id", ondelete="SET NULL"), index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(default=3, nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    error: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(255), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    input_artifact: Mapped[Artifact | None] = relationship(
        back_populates="jobs_as_input", foreign_keys=[input_artifact_id]
    )
    output_artifact: Mapped[Artifact | None] = relationship(foreign_keys=[output_artifact_id])
    recipe: Mapped[ConversionRecipe | None] = relationship(back_populates="jobs")
    batch_items: Mapped[list[ProcessingBatchItem]] = relationship(back_populates="job")


class ProcessingBatch(TimestampMixin, Base):
    __tablename__ = "processing_batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    scope_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by: Mapped[str | None] = mapped_column(String(255))
    label: Mapped[str | None] = mapped_column(String(255))

    items: Mapped[list[ProcessingBatchItem]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ProcessingBatchItem(TimestampMixin, Base):
    __tablename__ = "processing_batch_items"
    __table_args__ = (
        UniqueConstraint(
            "batch_id",
            "input_artifact_id",
            "recipe_id",
            name="uq_processing_batch_artifact_recipe",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("processing_batches.id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    input_artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    recipe_id: Mapped[str] = mapped_column(
        ForeignKey("conversion_recipes.id", ondelete="RESTRICT"), index=True
    )
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), index=True)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))

    batch: Mapped[ProcessingBatch] = relationship(back_populates="items")
    run: Mapped[Run] = relationship()
    recipe: Mapped[ConversionRecipe] = relationship(back_populates="batch_items")
    job: Mapped[Job | None] = relationship(back_populates="batch_items")


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[UserRole] = mapped_column(String(32), default=UserRole.VIEWER, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    memberships: Mapped[list[ProjectMembership]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    tokens: Mapped[list[ApiToken]] = relationship(back_populates="user", cascade="all, delete-orphan")


class LoginThrottle(TimestampMixin, Base):
    __tablename__ = "login_throttles"

    username: Mapped[str] = mapped_column(String(255), primary_key=True)
    failed_attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ProjectMembership(TimestampMixin, Base):
    __tablename__ = "project_memberships"
    __table_args__ = (UniqueConstraint("user_id", "project_id", name="uq_membership_user_project"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    role: Mapped[UserRole] = mapped_column(String(32), default=UserRole.VIEWER, nullable=False)

    user: Mapped[User] = relationship(back_populates="memberships")
    project: Mapped[Project] = relationship(back_populates="memberships")


class ApiToken(TimestampMixin, Base):
    __tablename__ = "api_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    kind: Mapped[TokenKind] = mapped_column(
        String(32), default=TokenKind.API, server_default=TokenKind.API.value, index=True, nullable=False
    )
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User | None] = relationship(back_populates="tokens")


class AuditLog(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(255), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(100))
    resource_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    request_id: Mapped[str | None] = mapped_column(String(100), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class ExtractionResult(TimestampMixin, Base):
    __tablename__ = "extraction_results"
    __table_args__ = (
        UniqueConstraint(
            "artifact_id",
            "schema_version",
            "extractor",
            "extractor_version",
            "result_type",
            name="uq_extraction_identity",
        ),
        Index("ix_extraction_artifact_created", "artifact_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(ForeignKey("artifacts.id", ondelete="CASCADE"), index=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    extractor: Mapped[str] = mapped_column(String(255), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    result_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    artifact: Mapped[Artifact] = relationship(back_populates="extraction_results")


class SpectrumCatalog(TimestampMixin, Base):
    __tablename__ = "spectrum_catalogs"
    __table_args__ = (
        Index("ix_spectrum_catalog_artifact_status", "artifact_id", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    artifact_id: Mapped[str] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE"), index=True
    )
    schema_version: Mapped[int] = mapped_column(default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="building", nullable=False)
    extractor: Mapped[str] = mapped_column(String(255), nullable=False)
    extractor_version: Mapped[str] = mapped_column(String(100), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    spectrum_count: Mapped[int] = mapped_column(default=0, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)

    artifact: Mapped[Artifact] = relationship(back_populates="spectrum_catalogs")
    entries: Mapped[list[SpectrumCatalogEntry]] = relationship(
        back_populates="catalog", cascade="all, delete-orphan"
    )


class SpectrumCatalogEntry(Base):
    __tablename__ = "spectrum_catalog_entries"
    __table_args__ = (
        UniqueConstraint("catalog_id", "ordinal", name="uq_spectrum_catalog_ordinal"),
        Index("ix_spectrum_entry_level_rt", "catalog_id", "ms_level", "retention_time_seconds", "ordinal"),
        Index("ix_spectrum_entry_scan", "catalog_id", "scan_number", "ordinal"),
        Index("ix_spectrum_entry_native", "catalog_id", "native_id"),
        Index("ix_spectrum_entry_precursor", "catalog_id", "ms_level", "precursor_mz", "ordinal"),
        Index("ix_spectrum_entry_charge_precursor", "catalog_id", "precursor_charge", "precursor_mz", "ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    catalog_id: Mapped[str] = mapped_column(
        ForeignKey("spectrum_catalogs.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    ms_level_index: Mapped[int] = mapped_column(nullable=False)
    native_id: Mapped[str | None] = mapped_column(String(2048))
    scan_number: Mapped[int | None] = mapped_column()
    ms_level: Mapped[int] = mapped_column(nullable=False)
    retention_time_seconds: Mapped[float | None] = mapped_column(Float)
    precursor_mz: Mapped[float | None] = mapped_column(Float)
    precursor_charge: Mapped[int | None] = mapped_column()
    neutral_mass: Mapped[float | None] = mapped_column(Float)
    isolation_lower_mz: Mapped[float | None] = mapped_column(Float)
    isolation_upper_mz: Mapped[float | None] = mapped_column(Float)
    peak_count: Mapped[int | None] = mapped_column()
    total_ion_current: Mapped[float | None] = mapped_column(Float)
    base_peak_mz: Mapped[float | None] = mapped_column(Float)
    base_peak_intensity: Mapped[float | None] = mapped_column(Float)
    mz_min: Mapped[float | None] = mapped_column(Float)
    mz_max: Mapped[float | None] = mapped_column(Float)
    polarity: Mapped[str | None] = mapped_column(String(32))
    representation: Mapped[str | None] = mapped_column(String(32))
    collision_energy: Mapped[float | None] = mapped_column(Float)
    activation_type: Mapped[str | None] = mapped_column(String(100))
    ion_mobility: Mapped[float | None] = mapped_column(Float)
    ion_mobility_unit: Mapped[str | None] = mapped_column(String(100))

    catalog: Mapped[SpectrumCatalog] = relationship(back_populates="entries")


class EventOutbox(Base):
    __tablename__ = "event_outbox"
    __table_args__ = (Index("ix_outbox_pending", "published_at", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(36), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AutomationRule(TimestampMixin, Base):
    __tablename__ = "automation_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    scope: Mapped[AutomationScope] = mapped_column(String(32), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(100), default="source_artifact_ready", nullable=False)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    priority: Mapped[int] = mapped_column(default=100, nullable=False)

    project: Mapped[Project | None] = relationship(back_populates="automation_rules")
    instrument: Mapped[Instrument | None] = relationship(back_populates="automation_rules")


class Agent(TimestampMixin, Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    registration_key: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    version: Mapped[str | None] = mapped_column(String(100))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="offline", nullable=False)
    capacity: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    destination_mode: Mapped[str] = mapped_column(
        String(32), default="inbox", server_default="inbox", nullable=False
    )
    destination_experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL"), index=True
    )

    upload_sessions: Mapped[list[UploadSession]] = relationship(back_populates="agent")


class UploadSession(TimestampMixin, Base):
    __tablename__ = "upload_sessions"
    __table_args__ = (UniqueConstraint("agent_id", "idempotency_key", name="uq_upload_agent_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(1024), nullable=False)
    format: Mapped[str] = mapped_column(String(32), nullable=False)
    role: Mapped[ArtifactRole] = mapped_column(String(32), default=ArtifactRole.SOURCE, nullable=False)
    total_size: Mapped[int | None] = mapped_column()
    expected_sha256: Mapped[str | None] = mapped_column(String(64))
    bundle_manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    offset: Mapped[int] = mapped_column(default=0, nullable=False)
    state: Mapped[UploadState] = mapped_column(String(32), default=UploadState.OPEN, nullable=False)
    temporary_path: Mapped[str | None] = mapped_column(String(2048))
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id", ondelete="SET NULL"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    agent: Mapped[Agent] = relationship(back_populates="upload_sessions")
    run: Mapped[Run] = relationship()
    artifact: Mapped[Artifact | None] = relationship()
    parts: Mapped[list[UploadPart]] = relationship(
        back_populates="upload_session", cascade="all, delete-orphan"
    )


class UploadPart(TimestampMixin, Base):
    __tablename__ = "upload_parts"
    __table_args__ = (UniqueConstraint("upload_session_id", "relative_path", name="uq_upload_part_path"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    upload_session_id: Mapped[str] = mapped_column(
        ForeignKey("upload_sessions.id", ondelete="CASCADE"), index=True
    )
    relative_path: Mapped[str] = mapped_column(String(2048), nullable=False)
    total_size: Mapped[int] = mapped_column(nullable=False)
    expected_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    offset: Mapped[int] = mapped_column(default=0, nullable=False)
    temporary_path: Mapped[str] = mapped_column(String(2048), nullable=False)

    upload_session: Mapped[UploadSession] = relationship(back_populates="parts")


class WebhookDestination(TimestampMixin, Base):
    __tablename__ = "webhook_destinations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    event_filters: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    signing_secret_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    signing_secret_salt: Mapped[str] = mapped_column(String(64), nullable=False)
    signing_secret_version: Mapped[int] = mapped_column(default=1, nullable=False)

    deliveries: Mapped[list[WebhookDelivery]] = relationship(
        back_populates="destination", cascade="all, delete-orphan"
    )


class WebhookDelivery(TimestampMixin, Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        UniqueConstraint("destination_id", "event_id", name="uq_webhook_delivery_event"),
        Index("ix_webhook_delivery_status", "status", "next_attempt_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    destination_id: Mapped[str] = mapped_column(
        ForeignKey("webhook_destinations.id", ondelete="CASCADE"), index=True
    )
    event_id: Mapped[str] = mapped_column(ForeignKey("event_outbox.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    attempts: Mapped[int] = mapped_column(default=0, nullable=False)
    response_status: Mapped[int | None] = mapped_column()
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(255), index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    destination: Mapped[WebhookDestination] = relationship(back_populates="deliveries")
    event: Mapped[EventOutbox] = relationship()
