"""MCP resource templates and tool schemas."""

RESOURCES = [
    {
        "uri": "spectarr://processing/profiles",
        "name": "Spectarr processing profiles",
        "description": "Named and revisioned MSConvert processing profiles",
        "mimeType": "application/json",
    },
    {
        "uri": "spectarr://processing/batches",
        "name": "Spectarr processing batches",
        "description": "Project, experiment, and run batch processing status",
        "mimeType": "application/json",
    },
    {
        "uri": "spectarr://automation/rules",
        "name": "Spectarr automation rules",
        "description": "Configured metadata and processing automation rules",
        "mimeType": "application/json",
    },
    {
        "uri": "spectarr://audit/status",
        "name": "Spectarr audit status",
        "description": "Audit log status visible to the configured credential",
        "mimeType": "application/json",
    },
    {
        "uri": "spectarr://events/outbox/status",
        "name": "Spectarr event outbox status",
        "description": "Publishing status for integration events",
        "mimeType": "application/json",
    },
]

RESOURCE_TEMPLATES = [
    {
        "uriTemplate": "spectarr://processing/batches/{batch_id}",
        "name": "Spectarr processing batch",
        "description": "Aggregate and item-level conversion progress for a batch",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://projects/{project_id}",
        "name": "Spectarr project",
        "description": "Project metadata from the Spectarr library",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://projects/{project_id}/library",
        "name": "Spectarr project filesystem library",
        "description": "Search-ready format directories and manifest paths for a project",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://projects/{project_id}/manifest",
        "name": "Spectarr project manifest",
        "description": "Runs, experiments, artifacts, checksums, and filesystem paths for a project",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://projects/{project_id}/sdrf",
        "name": "Project SDRF metadata",
        "description": "Ordered SDRF table, mappings, validation status, and provenance for a project",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://projects/{project_id}/submission",
        "name": "Project repository submission readiness",
        "description": "Repository package file counts, mappings, and SDRF readiness",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://runs/{run_id}",
        "name": "Spectarr run",
        "description": "Run metadata and source completeness",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://runs/{run_id}/artifacts",
        "name": "Spectarr run artifacts",
        "description": "Source and derived artifacts belonging to a run",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://jobs/{job_id}",
        "name": "Spectarr job",
        "description": "Import or conversion job state",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://artifacts/{artifact_id}/extraction/latest",
        "name": "Latest artifact extraction",
        "description": "Latest versioned metadata extraction for an artifact",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://runs/{run_id}/qc",
        "name": "Run QC summary",
        "description": "Latest normalized quality summary for a run",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "spectarr://instruments/{instrument_id}/agent-status",
        "name": "Instrument agent status",
        "description": "Connectivity and capacity of acquisition agents for an instrument",
        "mimeType": "application/json",
    },
]


TOOLS = [
    {
        "name": "get_project_sdrf",
        "description": "Get the exact ordered SDRF table and validation state for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_submission_readiness",
        "description": "Get repository package readiness, file counts, bytes, and unmapped SDRF rows.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "generate_project_sdrf",
        "description": "Replace project SDRF metadata from current runs and extracted metadata. Requires write mode and explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            "required": ["project_id", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "validate_project_sdrf",
        "description": "Validate a project SDRF document, optionally with ontology checks. Requires write mode and explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string"},
                "ontology": {"type": "boolean", "default": False},
                "confirm": {"type": "boolean"},
            },
            "required": ["project_id", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "list_processing_profiles",
        "description": "List named MSConvert profiles and their current revisions.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "list_processing_batches",
        "description": "List recent project, experiment, and run processing batches.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_processing_batch",
        "description": "Get aggregate progress and item errors for one processing batch.",
        "inputSchema": {
            "type": "object",
            "properties": {"batch_id": {"type": "string"}},
            "required": ["batch_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "queue_processing_batch",
        "description": "Queue named profiles for a project, selected experiments, or selected runs. Requires write mode and explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope_type": {"type": "string", "enum": ["project", "experiments", "runs"]},
                "scope_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 500},
                "recipe_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
                "mode": {"type": "string", "enum": ["missing", "missing_or_stale", "force"], "default": "missing"},
                "confirm": {"type": "boolean"},
            },
            "required": ["scope_type", "scope_ids", "recipe_ids", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "get_project_library",
        "description": "Get flat RAW, mzML, MGF, MS2, and other filesystem directories for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {"project_id": {"type": "string"}},
            "required": ["project_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "search_runs",
        "description": "Search and page through mass spectrometry runs in Spectarr.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_run",
        "description": "Get metadata and status for one run.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "list_run_artifacts",
        "description": "List source and generated artifacts for one run.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_job",
        "description": "Get progress and errors for an import or conversion job.",
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_latest_extraction",
        "description": "Get the latest versioned metadata extraction for an artifact.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "result_type": {"type": "string", "default": "metadata"},
            },
            "required": ["artifact_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_run_qc",
        "description": "Get the latest QC summary and extraction provenance for a run.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_instrument_agent_status",
        "description": "Get acquisition agent health and capacity for an instrument.",
        "inputSchema": {
            "type": "object",
            "properties": {"instrument_id": {"type": "string"}},
            "required": ["instrument_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "list_automation_rules",
        "description": "List configured automation rules and whether each is enabled.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_audit_status",
        "description": "Get an audit-log health summary. The API credential may require admin scope.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "get_outbox_status",
        "description": "Get event outbox publishing health and backlog status.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True, "openWorldHint": False},
    },
    {
        "name": "generate_derived_artifact",
        "description": (
            "Queue an allowlisted derivative recipe for a run. "
            "Requires write mode and explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "input_artifact_id": {"type": "string"},
                "recipe_id": {"type": "string"},
                "parameters": {"type": "object"},
                "confirm": {"type": "boolean", "description": "Must be true to queue processing."},
            },
            "required": ["run_id", "input_artifact_id", "recipe_id", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "add_run_annotation",
        "description": "Attach a note or QC annotation to a run. Requires write mode and explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "body": {"type": "string", "minLength": 1, "maxLength": 10000},
                "tags": {"type": "array", "items": {"type": "string"}},
                "author": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            "required": ["run_id", "body", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "retry_job",
        "description": "Request a retry for a failed job. Requires write mode and explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string"},
                "confirm": {"type": "boolean"},
            },
            "required": ["job_id", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
    {
        "name": "request_metadata_reextraction",
        "description": (
            "Queue metadata extraction for an artifact. Set force true to create a fresh extraction job. "
            "Requires write mode and explicit confirmation."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "artifact_id": {"type": "string"},
                "extractor": {
                    "type": "string",
                    "enum": ["spectarr-extractor"],
                    "default": "spectarr-extractor",
                },
                "schema_version": {"type": "string", "enum": ["1.0"], "default": "1.0"},
                "force": {"type": "boolean", "default": True},
                "confirm": {"type": "boolean"},
            },
            "required": ["artifact_id", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
    },
    {
        "name": "set_automation_rule_enabled",
        "description": "Enable or disable an automation rule. Requires write mode and explicit confirmation.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "rule_id": {"type": "string"},
                "enabled": {"type": "boolean"},
                "confirm": {"type": "boolean"},
            },
            "required": ["rule_id", "enabled", "confirm"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    },
]
