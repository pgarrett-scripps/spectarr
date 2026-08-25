"""MCP request handling backed exclusively by the Spectarr REST API."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

from . import __version__
from .catalog import RESOURCES, RESOURCE_TEMPLATES, TOOLS


class ApiClient(Protocol):
    def get(self, path: str, query: dict[str, Any] | None = None) -> Any: ...
    def post(self, path: str, payload: dict[str, Any]) -> Any: ...
    def patch(self, path: str, payload: dict[str, Any]) -> Any: ...


class SpectarrMcpServer:
    """Implement the MCP resources and tools needed by Spectarr clients."""

    def __init__(self, api: ApiClient, allow_writes: bool = False) -> None:
        self.api = api
        self.allow_writes = allow_writes

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if "id" not in message:
            return None
        message_id = message["id"]
        try:
            result = self.dispatch(str(message.get("method", "")), message.get("params") or {})
            return {"jsonrpc": "2.0", "id": message_id, "result": result}
        except Exception as error:
            return {
                "jsonrpc": "2.0",
                "id": message_id,
                "error": {"code": -32603, "message": str(error)},
            }

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            return {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"resources": {}, "tools": {}},
                "serverInfo": {"name": "spectarr-mcp", "version": __version__},
                "instructions": (
                    "Use read tools by default. "
                    "Write tools require explicit confirmation and server write mode."
                ),
            }
        if method == "ping":
            return {}
        if method == "resources/list":
            return {"resources": RESOURCES}
        if method == "resources/templates/list":
            return {"resourceTemplates": RESOURCE_TEMPLATES}
        if method == "resources/read":
            return self.read_resource(str(params["uri"]))
        if method == "tools/list":
            return {"tools": TOOLS}
        if method == "tools/call":
            return self.call_tool(str(params["name"]), params.get("arguments") or {})
        raise ValueError(f"Unsupported MCP method: {method}")

    def read_resource(self, uri: str) -> dict[str, Any]:
        parsed = urlparse(uri)
        if parsed.scheme != "spectarr":
            raise ValueError("Unsupported resource URI scheme")
        segments = [unquote(segment) for segment in ([parsed.netloc] + parsed.path.split("/")) if segment]
        if len(segments) == 2 and segments[0] == "projects":
            value = self.api.get(f"/api/v1/projects/{self._identifier(segments[1])}")
        elif len(segments) == 3 and segments[0] == "projects" and segments[2] in {"library", "manifest", "sdrf"}:
            value = self.api.get(
                f"/api/v1/projects/{self._identifier(segments[1])}/{segments[2]}"
            )
        elif len(segments) == 3 and segments[0] == "projects" and segments[2] == "submission":
            project_id = self._identifier(segments[1])
            value = self.api.get(f"/api/v1/projects/{project_id}/submission/preview")
        elif len(segments) == 2 and segments[0] == "runs":
            value = self.api.get(f"/api/v1/runs/{self._identifier(segments[1])}")
        elif len(segments) == 3 and segments[0] == "runs" and segments[2] == "artifacts":
            value = self.api.get(f"/api/v1/runs/{self._identifier(segments[1])}/artifacts")
        elif len(segments) == 2 and segments[0] == "jobs":
            value = self.api.get(f"/api/v1/jobs/{self._identifier(segments[1])}")
        elif len(segments) == 4 and segments[0] == "artifacts" and segments[2:] == ["extraction", "latest"]:
            artifact_id = self._identifier(segments[1])
            value = self.api.get(f"/api/v1/artifacts/{artifact_id}/extraction-results/latest")
        elif len(segments) == 3 and segments[0] == "runs" and segments[2] == "qc":
            value = self.api.get(f"/api/v1/runs/{self._identifier(segments[1])}/qc")
        elif len(segments) == 3 and segments[0] == "instruments" and segments[2] == "agent-status":
            value = self.api.get(f"/api/v1/instruments/{self._identifier(segments[1])}/agent-status")
        elif segments == ["automation", "rules"]:
            value = self.api.get("/api/v1/automation-rules")
        elif segments == ["processing", "profiles"]:
            value = self.api.get("/api/v1/recipes")
        elif segments == ["processing", "batches"]:
            value = self.api.get("/api/v1/processing-batches")
        elif len(segments) == 3 and segments[:2] == ["processing", "batches"]:
            value = self.api.get(f"/api/v1/processing-batches/{self._identifier(segments[2])}")
        elif segments == ["audit", "status"]:
            value = self.api.get("/api/v1/audit-log/status")
        elif segments == ["events", "outbox", "status"]:
            value = self.api.get("/api/v1/events/outbox/status")
        else:
            raise ValueError("Unknown Spectarr resource URI")
        return {
            "contents": [
                {"uri": uri, "mimeType": "application/json", "text": json.dumps(value, sort_keys=True)}
            ]
        }

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "get_project_sdrf":
            project_id = self._required_id(arguments, "project_id")
            value = self.api.get(f"/api/v1/projects/{project_id}/sdrf")
        elif name == "get_submission_readiness":
            project_id = self._required_id(arguments, "project_id")
            value = self.api.get(f"/api/v1/projects/{project_id}/submission/preview")
        elif name == "generate_project_sdrf":
            self._require_write(arguments)
            project_id = self._required_id(arguments, "project_id")
            value = self.api.post(f"/api/v1/projects/{project_id}/sdrf/generate", {})
        elif name == "validate_project_sdrf":
            self._require_write(arguments)
            project_id = self._required_id(arguments, "project_id")
            ontology = arguments.get("ontology", False)
            if not isinstance(ontology, bool):
                raise ValueError("ontology must be a boolean")
            value = self.api.post(
                f"/api/v1/projects/{project_id}/sdrf/validate",
                {"ontology": ontology},
            )
        elif name == "list_processing_profiles":
            value = self.api.get("/api/v1/recipes")
        elif name == "list_processing_batches":
            value = self.api.get("/api/v1/processing-batches")
        elif name == "get_processing_batch":
            batch_id = self._required_id(arguments, "batch_id")
            value = self.api.get(f"/api/v1/processing-batches/{batch_id}")
        elif name == "queue_processing_batch":
            self._require_write(arguments)
            scope_type = self._plain_string(arguments.get("scope_type"), "scope_type", 32)
            if scope_type not in {"project", "experiments", "runs"}:
                raise ValueError("Unsupported processing scope")
            mode = self._plain_string(arguments.get("mode", "missing"), "mode", 32)
            if mode not in {"missing", "missing_or_stale", "force"}:
                raise ValueError("Unsupported processing mode")
            value = self.api.post(
                "/api/v1/processing-batches",
                {
                    "scope_type": scope_type,
                    "scope_ids": self._id_list(arguments, "scope_ids", 500),
                    "recipe_ids": self._id_list(arguments, "recipe_ids", 20),
                    "mode": mode,
                },
            )
        elif name == "get_project_library":
            project_id = self._required_id(arguments, "project_id")
            value = self.api.get(f"/api/v1/projects/{project_id}/library")
        elif name == "search_runs":
            value = self.api.get(
                "/api/v1/runs",
                {
                    "query": arguments.get("query"),
                    "offset": arguments.get("offset", 0),
                    "limit": arguments.get("limit", 25),
                },
            )
        elif name == "get_run":
            value = self.api.get(f"/api/v1/runs/{self._required_id(arguments, 'run_id')}")
        elif name == "list_run_artifacts":
            value = self.api.get(f"/api/v1/runs/{self._required_id(arguments, 'run_id')}/artifacts")
        elif name == "get_job":
            value = self.api.get(f"/api/v1/jobs/{self._required_id(arguments, 'job_id')}")
        elif name == "get_latest_extraction":
            artifact_id = self._required_id(arguments, "artifact_id")
            result_type = self._plain_string(arguments.get("result_type", "metadata"), "result_type", 100)
            value = self.api.get(
                f"/api/v1/artifacts/{artifact_id}/extraction-results/latest",
                {"result_type": result_type},
            )
        elif name == "get_run_qc":
            value = self.api.get(f"/api/v1/runs/{self._required_id(arguments, 'run_id')}/qc")
        elif name == "get_instrument_agent_status":
            instrument_id = self._required_id(arguments, "instrument_id")
            value = self.api.get(f"/api/v1/instruments/{instrument_id}/agent-status")
        elif name == "list_automation_rules":
            value = self.api.get("/api/v1/automation-rules")
        elif name == "get_audit_status":
            value = self.api.get("/api/v1/audit-log/status")
        elif name == "get_outbox_status":
            value = self.api.get("/api/v1/events/outbox/status")
        elif name == "generate_derived_artifact":
            self._require_write(arguments)
            run_id = self._required_id(arguments, "run_id")
            value = self.api.post(
                f"/api/v1/runs/{run_id}/derivatives",
                {
                    "input_artifact_id": self._required_id(arguments, "input_artifact_id"),
                    "recipe_id": self._required_id(arguments, "recipe_id"),
                    "parameters": arguments.get("parameters") or {},
                },
            )
        elif name == "add_run_annotation":
            self._require_write(arguments)
            run_id = self._required_id(arguments, "run_id")
            body = str(arguments.get("body", "")).strip()
            if not body or len(body) > 10000:
                raise ValueError("Annotation text must contain 1 to 10000 characters")
            value = self.api.post(
                f"/api/v1/runs/{run_id}/annotations",
                {"body": body, "tags": arguments.get("tags") or [], "author": arguments.get("author")},
            )
        elif name == "retry_job":
            self._require_write(arguments)
            job_id = self._required_id(arguments, "job_id")
            value = self.api.post(f"/api/v1/jobs/{job_id}/retry", {})
        elif name == "request_metadata_reextraction":
            self._require_write(arguments)
            artifact_id = self._required_id(arguments, "artifact_id")
            extractor = self._plain_string(
                arguments.get("extractor", "spectarr-extractor"),
                "extractor",
                255,
            )
            if extractor != "spectarr-extractor":
                raise ValueError("Only spectarr-extractor is available")
            schema_version = self._plain_string(arguments.get("schema_version", "1.0"), "schema_version", 32)
            if not re.fullmatch(r"\d+\.\d+", schema_version) or schema_version != "1.0":
                raise ValueError("Only extraction schema version 1.0 is supported")
            force = arguments.get("force", True)
            if not isinstance(force, bool):
                raise ValueError("force must be a boolean")
            value = self.api.post(
                f"/api/v1/artifacts/{artifact_id}/extract",
                {"extractor": extractor, "schema_version": schema_version, "force": force},
            )
        elif name == "set_automation_rule_enabled":
            self._require_write(arguments)
            rule_id = self._required_id(arguments, "rule_id")
            enabled = arguments.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            value = self.api.patch(f"/api/v1/automation-rules/{rule_id}", {"enabled": enabled})
        else:
            raise ValueError(f"Unknown Spectarr tool: {name}")
        serialized = json.dumps(value, sort_keys=True)
        structured = value if isinstance(value, dict) else {"items": value}
        return {
            "content": [{"type": "text", "text": serialized}],
            "structuredContent": structured,
            "isError": False,
        }

    def _require_write(self, arguments: dict[str, Any]) -> None:
        if not self.allow_writes:
            raise PermissionError("MCP write tools are disabled on this server")
        if arguments.get("confirm") is not True:
            raise PermissionError("This write tool requires confirm=true")

    @classmethod
    def _required_id(cls, arguments: dict[str, Any], key: str) -> str:
        if key not in arguments:
            raise ValueError(f"Missing required argument: {key}")
        return cls._identifier(str(arguments[key]))

    @staticmethod
    def _identifier(value: str) -> str:
        if not value or len(value) > 128 or not all(character.isalnum() or character in "_.-" for character in value):
            raise ValueError("Invalid Spectarr identifier")
        return value

    @staticmethod
    def _plain_string(value: Any, name: str, maximum_length: int) -> str:
        text = str(value).strip()
        if not text or len(text) > maximum_length or any(ord(character) < 32 for character in text):
            raise ValueError(f"Invalid {name}")
        return text

    @classmethod
    def _id_list(cls, arguments: dict[str, Any], key: str, maximum_items: int) -> list[str]:
        values = arguments.get(key)
        if not isinstance(values, list) or not values or len(values) > maximum_items:
            raise ValueError(f"{key} must contain 1 to {maximum_items} identifiers")
        identifiers = [cls._identifier(str(value)) for value in values]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError(f"{key} must contain unique identifiers")
        return identifiers
