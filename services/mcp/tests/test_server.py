from __future__ import annotations

import unittest
import json
import threading
from typing import Any
from unittest.mock import patch
from urllib import request

from http.server import ThreadingHTTPServer

from spectarr_mcp.cli import make_http_handler
from spectarr_mcp.api import SpectarrApiClient
from spectarr_mcp.server import SpectarrMcpServer


class FakeApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, Any]] = []

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        self.calls.append(("GET", path, query))
        return {"path": path, "query": query}

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        self.calls.append(("POST", path, payload))
        return {"state": "queued", "path": path, "payload": payload}

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        self.calls.append(("PATCH", path, payload))
        return {"state": "queued", "path": path}


class FakeHttpResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self) -> bytes:
        return b"[]"


class SpectarrApiClientTests(unittest.TestCase):
    def test_worker_token_is_used_when_bearer_token_is_absent(self) -> None:
        client = SpectarrApiClient("http://spectarr.test", worker_token="worker-secret")
        with patch("spectarr_mcp.api.request.urlopen", return_value=FakeHttpResponse()) as opener:
            client.get("/api/v1/projects")
        headers = dict(opener.call_args.args[0].header_items())
        self.assertEqual(headers["X-spectarr-worker-token"], "worker-secret")

    def test_bearer_token_takes_precedence(self) -> None:
        client = SpectarrApiClient(
            "http://spectarr.test", api_key="service-secret", worker_token="worker-secret"
        )
        with patch("spectarr_mcp.api.request.urlopen", return_value=FakeHttpResponse()) as opener:
            client.get("/api/v1/projects")
        headers = dict(opener.call_args.args[0].header_items())
        self.assertEqual(headers["Authorization"], "Bearer service-secret")
        self.assertNotIn("X-spectarr-worker-token", headers)


class SpectarrMcpServerTests(unittest.TestCase):
    def test_initialize_advertises_resources_and_tools(self) -> None:
        server = SpectarrMcpServer(FakeApi())
        result = server.dispatch("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIn("resources", result["capabilities"])
        self.assertIn("tools", result["capabilities"])

    def test_read_run_resource_calls_public_api(self) -> None:
        api = FakeApi()
        result = SpectarrMcpServer(api).read_resource("spectarr://runs/run-123")
        self.assertEqual(api.calls[0], ("GET", "/api/v1/runs/run-123", None))
        self.assertEqual(result["contents"][0]["mimeType"], "application/json")

    def test_project_library_resources_and_tool_use_public_api(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api)
        server.read_resource("spectarr://projects/project-1/library")
        server.read_resource("spectarr://projects/project-1/manifest")
        server.call_tool("get_project_library", {"project_id": "project-1"})
        self.assertEqual(
            [call[1] for call in api.calls],
            [
                "/api/v1/projects/project-1/library",
                "/api/v1/projects/project-1/manifest",
                "/api/v1/projects/project-1/library",
            ],
        )

    def test_sdrf_resources_and_read_tools_use_public_api(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api)
        server.read_resource("spectarr://projects/project-1/sdrf")
        server.read_resource("spectarr://projects/project-1/submission")
        server.call_tool("get_project_sdrf", {"project_id": "project-1"})
        server.call_tool("get_submission_readiness", {"project_id": "project-1"})
        self.assertEqual(
            [call[1] for call in api.calls],
            [
                "/api/v1/projects/project-1/sdrf",
                "/api/v1/projects/project-1/submission/preview",
                "/api/v1/projects/project-1/sdrf",
                "/api/v1/projects/project-1/submission/preview",
            ],
        )

    def test_sdrf_write_tools_require_confirmation_and_call_api(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api, allow_writes=True)
        server.call_tool("generate_project_sdrf", {"project_id": "project-1", "confirm": True})
        server.call_tool(
            "validate_project_sdrf",
            {"project_id": "project-1", "ontology": True, "confirm": True},
        )
        self.assertEqual(
            api.calls,
            [
                ("POST", "/api/v1/projects/project-1/sdrf/generate", {}),
                ("POST", "/api/v1/projects/project-1/sdrf/validate", {"ontology": True}),
            ],
        )
        with self.assertRaises(PermissionError):
            server.call_tool("generate_project_sdrf", {"project_id": "project-1"})

    def test_read_latest_extraction_resource(self) -> None:
        api = FakeApi()
        SpectarrMcpServer(api).read_resource("spectarr://artifacts/artifact-1/extraction/latest")
        self.assertEqual(
            api.calls[0],
            ("GET", "/api/v1/artifacts/artifact-1/extraction-results/latest", None),
        )

    def test_read_run_qc_and_instrument_status_resources(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api)
        server.read_resource("spectarr://runs/run-1/qc")
        server.read_resource("spectarr://instruments/instrument-1/agent-status")
        self.assertEqual(api.calls[0], ("GET", "/api/v1/runs/run-1/qc", None))
        self.assertEqual(
            api.calls[1],
            ("GET", "/api/v1/instruments/instrument-1/agent-status", None),
        )

    def test_static_status_resources_are_listed_and_readable(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api)
        resources = server.dispatch("resources/list", {})["resources"]
        self.assertIn("spectarr://automation/rules", {resource["uri"] for resource in resources})
        server.read_resource("spectarr://automation/rules")
        server.read_resource("spectarr://audit/status")
        server.read_resource("spectarr://events/outbox/status")
        self.assertEqual(
            [call[1] for call in api.calls],
            [
                "/api/v1/automation-rules",
                "/api/v1/audit-log/status",
                "/api/v1/events/outbox/status",
            ],
        )

    def test_search_runs_is_read_only(self) -> None:
        api = FakeApi()
        result = SpectarrMcpServer(api).call_tool("search_runs", {"query": "hela"})
        self.assertFalse(result["isError"])
        self.assertEqual(api.calls[0][0], "GET")
        self.assertEqual(
            api.calls[0][2],
            {"query": "hela", "offset": 0, "limit": 25},
        )

    def test_processing_resources_and_read_tools_use_batch_api(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api)
        server.read_resource("spectarr://processing/profiles")
        server.read_resource("spectarr://processing/batches")
        server.read_resource("spectarr://processing/batches/batch-1")
        server.call_tool("list_processing_profiles", {})
        server.call_tool("list_processing_batches", {})
        server.call_tool("get_processing_batch", {"batch_id": "batch-1"})
        self.assertEqual(
            [call[1] for call in api.calls],
            [
                "/api/v1/recipes",
                "/api/v1/processing-batches",
                "/api/v1/processing-batches/batch-1",
                "/api/v1/recipes",
                "/api/v1/processing-batches",
                "/api/v1/processing-batches/batch-1",
            ],
        )

    def test_confirmed_processing_batch_uses_scoped_api(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api, allow_writes=True)
        server.call_tool(
            "queue_processing_batch",
            {
                "scope_type": "experiments",
                "scope_ids": ["experiment-1", "experiment-2"],
                "recipe_ids": ["recipe-1"],
                "mode": "missing_or_stale",
                "confirm": True,
            },
        )
        self.assertEqual(
            api.calls[0],
            (
                "POST",
                "/api/v1/processing-batches",
                {
                    "scope_type": "experiments",
                    "scope_ids": ["experiment-1", "experiment-2"],
                    "recipe_ids": ["recipe-1"],
                    "mode": "missing_or_stale",
                },
            ),
        )

    def test_write_is_disabled_by_default(self) -> None:
        server = SpectarrMcpServer(FakeApi())
        with self.assertRaisesRegex(PermissionError, "disabled"):
            server.call_tool(
                "generate_derived_artifact",
                {
                    "run_id": "run-1",
                    "input_artifact_id": "artifact-1",
                    "recipe_id": "recipe-1",
                    "confirm": True,
                },
            )

    def test_conversion_requires_confirmation(self) -> None:
        server = SpectarrMcpServer(FakeApi(), allow_writes=True)
        with self.assertRaisesRegex(PermissionError, "confirm"):
            server.call_tool(
                "generate_derived_artifact",
                {"run_id": "run-1", "input_artifact_id": "artifact-1", "recipe_id": "recipe-1"},
            )

    def test_confirmed_conversion_uses_derivative_endpoint(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api, allow_writes=True)
        server.call_tool(
            "generate_derived_artifact",
            {
                "run_id": "run-1",
                "input_artifact_id": "artifact-1",
                "recipe_id": "recipe-1",
                "confirm": True,
            },
        )
        self.assertEqual(api.calls[0][0:2], ("POST", "/api/v1/runs/run-1/derivatives"))

    def test_path_injection_is_rejected(self) -> None:
        server = SpectarrMcpServer(FakeApi())
        with self.assertRaisesRegex(ValueError, "Invalid"):
            server.call_tool("get_run", {"run_id": "../settings"})

    def test_annotation_uses_backend_schema(self) -> None:
        api = FakeApi()
        SpectarrMcpServer(api, allow_writes=True).call_tool(
            "add_run_annotation",
            {"run_id": "run-1", "body": "Looks good", "tags": ["qc"], "author": "agent", "confirm": True},
        )
        self.assertEqual(
            api.calls[0],
            (
                "POST",
                "/api/v1/runs/run-1/annotations",
                {"body": "Looks good", "tags": ["qc"], "author": "agent"},
            ),
        )

    def test_retry_uses_retry_endpoint(self) -> None:
        api = FakeApi()
        SpectarrMcpServer(api, allow_writes=True).call_tool(
            "retry_job", {"job_id": "job-1", "confirm": True}
        )
        self.assertEqual(api.calls[0], ("POST", "/api/v1/jobs/job-1/retry", {}))

    def test_new_read_tools_use_status_endpoints(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api)
        server.call_tool("get_latest_extraction", {"artifact_id": "artifact-1"})
        server.call_tool("get_run_qc", {"run_id": "run-1"})
        server.call_tool("get_instrument_agent_status", {"instrument_id": "instrument-1"})
        server.call_tool("list_automation_rules", {})
        server.call_tool("get_audit_status", {})
        server.call_tool("get_outbox_status", {})
        self.assertEqual(
            [call[1] for call in api.calls],
            [
                "/api/v1/artifacts/artifact-1/extraction-results/latest",
                "/api/v1/runs/run-1/qc",
                "/api/v1/instruments/instrument-1/agent-status",
                "/api/v1/automation-rules",
                "/api/v1/audit-log/status",
                "/api/v1/events/outbox/status",
            ],
        )
        self.assertEqual(api.calls[0][2], {"result_type": "metadata"})

    def test_reextraction_is_confirmed_and_forced_by_default(self) -> None:
        api = FakeApi()
        server = SpectarrMcpServer(api, allow_writes=True)
        server.call_tool(
            "request_metadata_reextraction",
            {"artifact_id": "artifact-1", "confirm": True},
        )
        self.assertEqual(
            api.calls[0],
            (
                "POST",
                "/api/v1/artifacts/artifact-1/extract",
                {"extractor": "spectarr-extractor", "schema_version": "1.0", "force": True},
            ),
        )

    def test_reextraction_write_gate_is_enforced(self) -> None:
        server = SpectarrMcpServer(FakeApi())
        with self.assertRaisesRegex(PermissionError, "disabled"):
            server.call_tool(
                "request_metadata_reextraction",
                {"artifact_id": "artifact-1", "confirm": True},
            )

    def test_reextraction_rejects_unsupported_extractor(self) -> None:
        server = SpectarrMcpServer(FakeApi(), allow_writes=True)
        with self.assertRaisesRegex(ValueError, "Only spectarr-extractor"):
            server.call_tool(
                "request_metadata_reextraction",
                {"artifact_id": "artifact-1", "extractor": "shell", "confirm": True},
            )

    def test_automation_toggle_requires_confirmation(self) -> None:
        server = SpectarrMcpServer(FakeApi(), allow_writes=True)
        with self.assertRaisesRegex(PermissionError, "confirm"):
            server.call_tool("set_automation_rule_enabled", {"rule_id": "rule-1", "enabled": False})

    def test_automation_toggle_patches_enabled_only(self) -> None:
        api = FakeApi()
        SpectarrMcpServer(api, allow_writes=True).call_tool(
            "set_automation_rule_enabled",
            {"rule_id": "rule-1", "enabled": False, "confirm": True},
        )
        self.assertEqual(
            api.calls[0],
            ("PATCH", "/api/v1/automation-rules/rule-1", {"enabled": False}),
        )

    def test_streamable_http_post(self) -> None:
        try:
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_http_handler(SpectarrMcpServer(FakeApi())))
        except PermissionError:
            self.skipTest("Local sockets are disabled in this test environment")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            payload = json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}
            ).encode()
            http_request = request.Request(
                f"http://127.0.0.1:{server.server_port}/mcp",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with request.urlopen(http_request, timeout=2) as response:
                result = json.loads(response.read())
            self.assertEqual(result["result"]["serverInfo"]["name"], "spectarr-mcp")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
