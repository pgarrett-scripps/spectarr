from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import (
    ArtifactRole,
    ArtifactState,
    AutomationScope,
    JobKind,
    JobState,
    SourceClass,
    UploadState,
    UserRole,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    metadata_json: dict[str, Any] | None = None


class ProjectRead(ApiModel):
    id: str
    name: str
    description: str | None
    system_key: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ExperimentCreate(BaseModel):
    project_id: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None


class ExperimentRead(ApiModel):
    id: str
    project_id: str
    name: str
    description: str | None
    intake_agent_id: str | None
    created_at: datetime
    updated_at: datetime


class SampleCreate(BaseModel):
    experiment_id: str
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class SampleRead(ApiModel):
    id: str
    experiment_id: str
    name: str
    description: str | None
    metadata_json: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class InstrumentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    enabled: bool = True


class InstrumentRead(ApiModel):
    id: str
    name: str
    vendor: str | None
    model: str | None
    serial_number: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


class RunSampleCreate(BaseModel):
    sample_id: str
    label: str = Field(default="label free sample", min_length=1, max_length=255)
    role: str = Field(default="analyte", min_length=1, max_length=64)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class RunSampleRead(ApiModel):
    id: str
    sample_id: str
    position: int
    label: str
    role: str
    metadata_json: dict[str, Any]


class RunCreate(BaseModel):
    experiment_id: str
    sample_id: str | None = None
    instrument_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    source_class: SourceClass = SourceClass.UNKNOWN
    acquired_at: datetime | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    samples: list[RunSampleCreate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_sample_inputs(self) -> RunCreate:
        if self.sample_id and self.samples:
            raise ValueError("Use sample_id or samples, not both")
        return self


class RunRead(ApiModel):
    id: str
    experiment_id: str
    sample_id: str | None
    sample_links: list[RunSampleRead] = Field(default_factory=list)
    instrument_id: str | None
    name: str
    source_class: SourceClass
    acquired_at: datetime | None
    metadata_json: dict[str, Any]
    assignment_status: str
    created_at: datetime
    updated_at: datetime


class PeakPickingFilter(BaseModel):
    kind: Literal["peak_picking"]
    algorithm: str = "vendor"
    ms_levels: list[int] = Field(default_factory=lambda: [1, 2])


class MsLevelFilter(BaseModel):
    kind: Literal["ms_level"]
    levels: list[int] = Field(min_length=1)


class ThresholdFilter(BaseModel):
    kind: Literal["threshold"]
    threshold_type: Literal["count", "absolute", "relative"]
    value: float
    orientation: Literal["most-intense", "least-intense"] = "most-intense"


ConversionFilter = PeakPickingFilter | MsLevelFilter | ThresholdFilter


class ConversionParameters(BaseModel):
    preset: Literal["sage", "biosaur", "blitzff", "casanovo", "casanovo_mgf"] | None = None
    filters: list[ConversionFilter] = Field(default_factory=list)
    mz_precision: Literal[32, 64] = 64
    intensity_precision: Literal[32, 64] = 32
    compression: Literal["none", "zlib", "numpress"] = "zlib"
    indexed: bool = True


class RecipeCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    converter: str = "msconvert"
    converter_version: str | None = None
    output_format: Literal["mzML", "mzXML", "MGF", "MS2"]
    parameters: ConversionParameters = Field(default_factory=ConversionParameters)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_preset_format(self) -> RecipeCreate:
        expected = "MGF" if self.parameters.preset == "casanovo_mgf" else "mzML"
        if self.parameters.preset and self.output_format != expected:
            raise ValueError(f"{self.parameters.preset} produces {expected}")
        return self


class RecipeRead(ApiModel):
    id: str
    name: str
    converter: str
    converter_version: str | None
    output_format: str
    parameters: dict[str, Any]
    description: str | None
    revision: int
    system: bool
    enabled: bool
    created_at: datetime
    updated_at: datetime


class RecipeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    output_format: Literal["mzML", "mzXML", "MGF", "MS2"] | None = None
    parameters: ConversionParameters | None = None
    enabled: bool | None = None


class ProcessingBatchRequest(BaseModel):
    scope_type: Literal["project", "experiments", "runs"]
    scope_ids: list[str] = Field(min_length=1, max_length=500)
    recipe_ids: list[str] = Field(min_length=1, max_length=20)
    mode: Literal["missing", "missing_or_stale", "force"] = "missing"
    label: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_unique_ids(self) -> ProcessingBatchRequest:
        if len(set(self.scope_ids)) != len(self.scope_ids):
            raise ValueError("scope_ids must be unique")
        if len(set(self.recipe_ids)) != len(self.recipe_ids):
            raise ValueError("recipe_ids must be unique")
        if self.scope_type == "project" and len(self.scope_ids) != 1:
            raise ValueError("Project scope requires exactly one project ID")
        return self


class ProcessingBatchPreview(BaseModel):
    scope_type: str
    run_count: int
    target_count: int
    queue_count: int
    current_count: int
    stale_count: int
    incompatible_count: int
    queued_count: int


class ProcessingBatchItemRead(BaseModel):
    id: str
    run_id: str
    run_name: str
    input_artifact_id: str
    recipe_id: str
    recipe_name: str
    output_format: str
    job_id: str | None
    disposition: str
    reason: str | None
    state: str
    progress: float
    error: str | None


class ProcessingBatchRead(BaseModel):
    id: str
    scope_type: str
    scope_ids: list[str]
    mode: str
    requested_by: str | None
    label: str | None
    state: str
    total_count: int
    queued_count: int
    running_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    cancelled_count: int
    progress: float
    created_at: datetime
    updated_at: datetime
    items: list[ProcessingBatchItemRead] = Field(default_factory=list)


class ArtifactRead(ApiModel):
    id: str
    run_id: str
    parent_artifact_id: str | None
    recipe_id: str | None
    role: ArtifactRole
    state: ArtifactState
    format: str
    original_filename: str
    storage_key: str
    library_path: str | None
    materialization_mode: str | None
    byte_size: int
    sha256: str
    bundle_manifest: dict[str, Any] | None
    recipe_fingerprint: str | None
    metadata_json: dict[str, Any]
    immutable: bool
    created_at: datetime
    updated_at: datetime


class PathImportRequest(BaseModel):
    source_path: str
    role: ArtifactRole = ArtifactRole.SOURCE
    format: str | None = None
    parent_artifact_id: str | None = None
    recipe_id: str | None = None
    recipe_fingerprint: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class DerivativeRequest(BaseModel):
    input_artifact_id: str | None = None
    recipe_id: str | None = None
    format: Literal["mzML", "mzXML", "MGF", "MS2"] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_recipe_or_format(self) -> DerivativeRequest:
        if not self.recipe_id and not self.format:
            raise ValueError("Either recipe_id or format is required")
        return self


class JobCreate(BaseModel):
    kind: JobKind
    input_artifact_id: str | None = None
    recipe_id: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    max_attempts: int = Field(default=3, ge=1, le=20)


class JobUpdate(BaseModel):
    state: JobState | None = None
    progress: float | None = Field(default=None, ge=0, le=1)
    output_artifact_id: str | None = None
    error: str | None = None
    attempts: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> JobUpdate:
        if self.state == JobState.FAILED and not self.error:
            raise ValueError("A failed job requires an error")
        return self


class JobRead(ApiModel):
    id: str
    kind: JobKind
    state: JobState
    input_artifact_id: str | None
    output_artifact_id: str | None
    recipe_id: str | None
    progress: float
    attempts: int
    max_attempts: int
    parameters: dict[str, Any]
    error: str | None
    worker_id: str | None
    lease_expires_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AnnotationCreate(BaseModel):
    author: str | None = None
    body: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)


class AnnotationRead(ApiModel):
    id: str
    run_id: str
    author: str | None
    body: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


class HealthRead(BaseModel):
    status: Literal["ok"] = "ok"
    database: Literal["ok"] = "ok"
    storage: Literal["ok"] = "ok"
    version: str


class BootstrapRequest(BaseModel):
    username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=12, max_length=1024)


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthConfiguration(BaseModel):
    mode: Literal["password", "local"]
    local_user: str | None = None
    allow_remote_no_auth: bool = False


class UserCreate(BootstrapRequest):
    display_name: str | None = Field(default=None, max_length=255)
    role: UserRole = UserRole.VIEWER
    active: bool = True


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=255)
    role: UserRole | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=1024)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=1024)
    new_password: str = Field(min_length=12, max_length=1024)


class UserRead(ApiModel):
    id: str
    username: str
    display_name: str | None
    role: UserRole
    active: bool
    last_login_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    user_id: str | None = None
    scopes: list[str] = Field(default_factory=lambda: ["library:read"])
    expires_at: datetime | None = None


class TokenRead(ApiModel):
    id: str
    user_id: str | None
    name: str
    token_prefix: str
    scopes: list[str]
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class TokenCreated(TokenRead):
    token: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    user: UserRead


class MembershipCreate(BaseModel):
    user_id: str
    role: UserRole = UserRole.VIEWER


class MembershipRead(ApiModel):
    id: str
    user_id: str
    project_id: str
    role: UserRole
    created_at: datetime
    updated_at: datetime


class ExtractionResultCreate(BaseModel):
    schema_version: str = Field(pattern=r"^\d+\.\d+$")
    extractor: str = Field(min_length=1, max_length=255)
    extractor_version: str = Field(min_length=1, max_length=100)
    result_type: str = Field(default="metadata", min_length=1, max_length=100)
    payload: dict[str, Any]
    warnings: list[str] = Field(default_factory=list)


class ExtractionResultRead(ApiModel):
    id: str
    artifact_id: str
    schema_version: str
    extractor: str
    extractor_version: str
    result_type: str
    payload: dict[str, Any]
    warnings: list[str]
    created_at: datetime
    updated_at: datetime


class SpectrumCatalogCreate(BaseModel):
    extractor: str = Field(min_length=1, max_length=255)
    extractor_version: str = Field(min_length=1, max_length=100)
    schema_version: int = Field(default=1, ge=1, le=100)


class SpectrumCatalogEntryCreate(BaseModel):
    ordinal: int = Field(ge=0)
    ms_level_index: int = Field(ge=0)
    native_id: str | None = Field(default=None, max_length=2048)
    scan_number: int | None = Field(default=None, ge=0)
    ms_level: int = Field(ge=1, le=100)
    retention_time_seconds: float | None = Field(default=None, ge=0)
    precursor_mz: float | None = Field(default=None, ge=0)
    precursor_charge: int | None = Field(default=None, ge=-100, le=100)
    neutral_mass: float | None = Field(default=None, ge=0)
    isolation_lower_mz: float | None = Field(default=None, ge=0)
    isolation_upper_mz: float | None = Field(default=None, ge=0)
    peak_count: int | None = Field(default=None, ge=0)
    total_ion_current: float | None = Field(default=None, ge=0)
    base_peak_mz: float | None = Field(default=None, ge=0)
    base_peak_intensity: float | None = Field(default=None, ge=0)
    mz_min: float | None = Field(default=None, ge=0)
    mz_max: float | None = Field(default=None, ge=0)
    polarity: str | None = Field(default=None, max_length=32)
    representation: str | None = Field(default=None, max_length=32)
    collision_energy: float | None = None
    activation_type: str | None = Field(default=None, max_length=100)
    ion_mobility: float | None = None
    ion_mobility_unit: str | None = Field(default=None, max_length=100)


class SpectrumCatalogBatch(BaseModel):
    entries: list[SpectrumCatalogEntryCreate] = Field(min_length=1, max_length=1000)


class SpectrumCatalogComplete(BaseModel):
    spectrum_count: int = Field(ge=0)


class SpectrumCatalogFail(BaseModel):
    error: str = Field(min_length=1, max_length=10000)


class SpectrumQuery(BaseModel):
    ms_levels: list[int] = Field(default_factory=list, max_length=20)
    scan_number_min: int | None = Field(default=None, ge=0)
    scan_number_max: int | None = Field(default=None, ge=0)
    retention_time_min: float | None = Field(default=None, ge=0)
    retention_time_max: float | None = Field(default=None, ge=0)
    precursor_mz_min: float | None = Field(default=None, ge=0)
    precursor_mz_max: float | None = Field(default=None, ge=0)
    neutral_mass_min: float | None = Field(default=None, ge=0)
    neutral_mass_max: float | None = Field(default=None, ge=0)
    charges: list[int] = Field(default_factory=list, max_length=100)
    peak_count_min: int | None = Field(default=None, ge=0)
    peak_count_max: int | None = Field(default=None, ge=0)
    total_ion_current_min: float | None = Field(default=None, ge=0)
    total_ion_current_max: float | None = Field(default=None, ge=0)
    base_peak_mz_min: float | None = Field(default=None, ge=0)
    base_peak_mz_max: float | None = Field(default=None, ge=0)
    native_id: str | None = Field(default=None, max_length=2048)
    polarities: list[str] = Field(default_factory=list, max_length=10)
    representations: list[str] = Field(default_factory=list, max_length=10)
    sort: Literal[
        "ordinal",
        "scan_number",
        "retention_time_seconds",
        "ms_level",
        "precursor_mz",
        "neutral_mass",
        "peak_count",
        "total_ion_current",
        "base_peak_mz",
    ] = "retention_time_seconds"
    direction: Literal["asc", "desc"] = "asc"
    cursor: str | None = Field(default=None, max_length=4096)
    limit: int = Field(default=50, ge=1, le=100)

    @model_validator(mode="after")
    def validate_ranges(self) -> "SpectrumQuery":
        for prefix in (
            "scan_number",
            "retention_time",
            "precursor_mz",
            "neutral_mass",
            "peak_count",
            "total_ion_current",
            "base_peak_mz",
        ):
            minimum = getattr(self, f"{prefix}_min")
            maximum = getattr(self, f"{prefix}_max")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"{prefix}_min must not exceed {prefix}_max")
        return self


class ExtractRequest(BaseModel):
    extractor: str = "spectarr-extractor"
    schema_version: str = "1.0"
    force: bool = False


class AutomationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    enabled: bool = True
    scope: AutomationScope = AutomationScope.GLOBAL
    project_id: str | None = None
    instrument_id: str | None = None
    trigger: Literal["source_artifact_ready"] = "source_artifact_ready"
    actions: list[dict[str, Any]] = Field(min_length=1)
    priority: int = Field(default=100, ge=0, le=10000)

    @model_validator(mode="after")
    def validate_scope_target(self) -> AutomationRuleCreate:
        if self.scope == AutomationScope.PROJECT and not self.project_id:
            raise ValueError("Project scope requires project_id")
        if self.scope == AutomationScope.INSTRUMENT and not self.instrument_id:
            raise ValueError("Instrument scope requires instrument_id")
        if self.scope == AutomationScope.GLOBAL and (self.project_id or self.instrument_id):
            raise ValueError("Global scope cannot have a project or instrument target")
        return self


class AutomationRuleUpdate(BaseModel):
    enabled: bool | None = None
    actions: list[dict[str, Any]] | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)


class AutomationRuleRead(ApiModel):
    id: str
    name: str
    enabled: bool
    scope: AutomationScope
    project_id: str | None
    instrument_id: str | None
    trigger: str
    actions: list[dict[str, Any]]
    priority: int
    created_at: datetime
    updated_at: datetime


class AgentRegister(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    version: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    destination_experiment_id: str | None = None


class AgentUpdate(BaseModel):
    destination_mode: Literal["inbox", "direct"] | None = None
    destination_experiment_id: str | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def validate_destination(self) -> AgentUpdate:
        if self.destination_mode == "direct" and not self.destination_experiment_id:
            raise ValueError("Direct destination requires destination_experiment_id")
        if self.destination_mode is None and self.destination_experiment_id is not None:
            raise ValueError("destination_experiment_id requires destination_mode")
        if self.destination_mode is None and self.enabled is None:
            raise ValueError("At least one agent setting is required")
        return self


class AgentHeartbeat(BaseModel):
    status: str = "online"
    capacity: dict[str, Any] = Field(default_factory=dict)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class AgentRead(ApiModel):
    id: str
    name: str
    version: str | None
    capabilities: list[str]
    metadata_json: dict[str, Any]
    status: str
    capacity: dict[str, Any]
    enabled: bool
    last_seen_at: datetime | None
    destination_mode: str
    destination_experiment_id: str | None
    created_at: datetime
    updated_at: datetime


class AgentRegistered(AgentRead):
    token: str


class BundleFile(BaseModel):
    path: str = Field(min_length=1, max_length=2048)
    size: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-fA-F]{64}$")


class BundleUploadManifest(BaseModel):
    root_name: str = Field(min_length=1, max_length=1024)
    files: list[BundleFile] = Field(min_length=1)


class UploadRunCreate(BaseModel):
    experiment_id: str | None = None
    sample_id: str | None = None
    instrument_id: str | None = None
    name: str = Field(min_length=1, max_length=255)
    source_class: SourceClass = SourceClass.UNKNOWN
    acquired_at: datetime | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class UploadSessionCreate(BaseModel):
    run_id: str | None = None
    run: UploadRunCreate | None = None
    filename: str = Field(min_length=1, max_length=1024)
    format: str
    role: ArtifactRole = ArtifactRole.SOURCE
    total_size: int | None = Field(default=None, ge=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    bundle_manifest: BundleUploadManifest | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_upload_shape(self) -> UploadSessionCreate:
        if bool(self.run_id) == bool(self.run):
            raise ValueError("Exactly one of run_id or run is required")
        if self.bundle_manifest:
            if self.bundle_manifest.root_name != self.filename:
                raise ValueError("Bundle filename must match bundle_manifest.root_name")
            if self.total_size is not None or self.sha256 is not None:
                raise ValueError("Bundle uploads use the bundle manifest instead of file size and checksum")
        elif self.total_size is None or self.sha256 is None:
            raise ValueError("File uploads require total_size and sha256")
        return self


class RunAssignmentUpdate(BaseModel):
    experiment_id: str
    sample_id: str | None = None


class BulkRunAssignment(BaseModel):
    run_ids: list[str] = Field(min_length=1, max_length=500)
    experiment_id: str
    sample_id: str | None = None


class UploadPartRead(ApiModel):
    path: str
    size: int
    offset: int
    state: str


class UploadSessionRead(ApiModel):
    id: str
    agent_id: str
    run_id: str
    filename: str
    format: str
    role: ArtifactRole
    total_size: int | None
    expected_sha256: str | None
    offset: int
    state: UploadState
    artifact_id: str | None
    expires_at: datetime
    files: list[UploadPartRead] = Field(default_factory=list)


class AuditLogRead(ApiModel):
    id: str
    actor_type: str
    actor_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    project_id: str | None
    details: dict[str, Any]
    request_id: str | None
    created_at: datetime


class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    url: str = Field(pattern=r"^https?://", max_length=2048)
    event_filters: list[str] = Field(default_factory=list)
    enabled: bool = True


class WebhookUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    url: str | None = Field(default=None, pattern=r"^https?://", max_length=2048)
    event_filters: list[str] | None = None
    enabled: bool | None = None
    rotate_secret: bool = False


class WebhookRead(ApiModel):
    id: str
    name: str
    url: str
    event_filters: list[str]
    enabled: bool
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryRead(ApiModel):
    id: str
    destination_id: str
    event_id: str
    status: str
    attempts: int
    response_status: int | None
    last_error: str | None
    next_attempt_at: datetime | None
    worker_id: str | None
    lease_expires_at: datetime | None
    delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ExperimentDeletionPreview(BaseModel):
    experiment_id: str
    experiment_name: str
    run_count: int
    source_count: int
    derived_count: int
    logical_bytes: int


class DerivedPurgePreviewRequest(BaseModel):
    scope_type: Literal["project", "experiments", "runs"]
    scope_ids: list[str] = Field(min_length=1, max_length=500)
    formats: list[Literal["mzML", "mzXML", "MGF", "MS2"]] = Field(
        default_factory=lambda: ["mzML", "mzXML", "MGF", "MS2"]
    )

    @model_validator(mode="after")
    def validate_scope(self) -> DerivedPurgePreviewRequest:
        if self.scope_type == "project" and len(self.scope_ids) != 1:
            raise ValueError("Project scope requires exactly one project ID")
        if len(set(self.scope_ids)) != len(self.scope_ids):
            raise ValueError("Scope IDs must be unique")
        if len(set(self.formats)) != len(self.formats):
            raise ValueError("Formats must be unique")
        return self


class DerivedPurgeRequest(DerivedPurgePreviewRequest):
    confirmation: Literal["PURGE DERIVED FILES"]


class StorageReclaimRead(BaseModel):
    artifact_count: int
    reclaimable_bytes: int
    format_counts: dict[str, int]
    blocked_count: int = 0


class SdrfRowWrite(BaseModel):
    values: list[str]
    sample_id: str | None = None
    run_id: str | None = None
    artifact_id: str | None = None


class SdrfDocumentWrite(BaseModel):
    columns: list[str] = Field(min_length=1, max_length=500)
    rows: list[SdrfRowWrite] = Field(default_factory=list, max_length=100000)
    templates: list[str] = Field(default_factory=lambda: ["ms-proteomics v1.1.0"])
    source_filename: str | None = Field(default=None, max_length=1024)
    synchronize: bool = True

    @model_validator(mode="after")
    def validate_table_shape(self) -> SdrfDocumentWrite:
        if any(not column.strip() for column in self.columns):
            raise ValueError("SDRF column names cannot be empty")
        for index, row in enumerate(self.rows, start=1):
            if len(row.values) != len(self.columns):
                raise ValueError(f"Row {index} does not match the SDRF column count")
        return self


class SdrfValidationRequest(BaseModel):
    ontology: bool = False


class SubmissionPreviewRead(BaseModel):
    project_id: str
    source_count: int
    derivative_count: int
    total_bytes: int
    sdrf_status: str
    sdrf_revision: int | None
    mapped_rows: int
    unmapped_rows: int
    ready: bool
