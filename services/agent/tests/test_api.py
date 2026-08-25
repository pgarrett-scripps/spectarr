from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from spectarr_agent.api import SpectarrAgentApi


class FakeResponse:
    def __init__(self, content: bytes = b"", headers: dict[str, str] | None = None) -> None:
        self.content = content
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.content


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.api = SpectarrAgentApi("https://spectarr.example")

    def test_registration_uses_admin_bearer_and_records_capabilities(self) -> None:
        captured = []

        def open_request(value, timeout):
            captured.append((value, timeout))
            return FakeResponse(json.dumps({"id": "agent-1", "token": "agt_secret"}).encode())

        with patch("spectarr_agent.api.request.urlopen", side_effect=open_request):
            registration = self.api.register("admin-token", "instrument-pc", "local-id")
        self.assertEqual(registration.token, "agt_secret")
        api_request = captured[0][0]
        self.assertEqual(api_request.full_url, "https://spectarr.example/api/v1/agents/register")
        self.assertEqual(api_request.get_header("Authorization"), "Bearer admin-token")
        payload = json.loads(api_request.data)
        self.assertIn("resumable_upload", payload["capabilities"])

    def test_chunk_uses_exact_offset_and_idempotency_headers(self) -> None:
        captured = []

        def open_request(value, timeout):
            captured.append(value)
            return FakeResponse(headers={"Upload-Offset": "8"})

        with patch("spectarr_agent.api.request.urlopen", side_effect=open_request):
            offset = self.api.upload_chunk("agt_secret", "upload-1", 5, b"abc")
        self.assertEqual(offset, 8)
        api_request = captured[0]
        self.assertEqual(api_request.full_url, "https://spectarr.example/api/v1/upload-sessions/upload-1")
        self.assertEqual(api_request.get_header("Upload-offset"), "5")
        self.assertEqual(api_request.get_header("Idempotency-key"), "upload-1:5:3")
        self.assertEqual(api_request.data, b"abc")

    def test_bundle_path_is_encoded_without_losing_directories(self) -> None:
        captured = []

        def open_request(value, timeout):
            captured.append(value)
            return FakeResponse(headers={"Upload-Offset": "1"})

        with patch("spectarr_agent.api.request.urlopen", side_effect=open_request):
            self.api.upload_bundle_chunk("agt_secret", "upload-1", "Acq Data/a+b.bin", 0, b"x")
        self.assertEqual(
            captured[0].full_url,
            "https://spectarr.example/api/v1/upload-sessions/upload-1/files/Acq%20Data/a%2Bb.bin",
        )

    def test_create_bundle_session_matches_inline_run_contract(self) -> None:
        captured = []

        def open_request(value, timeout):
            captured.append(value)
            return FakeResponse(
                json.dumps({"id": "upload-1", "state": "open", "files": []}).encode()
            )

        manifest = {
            "root_name": "sample.d",
            "files": [{"path": "data.bin", "size": 1, "sha256": "a" * 64}],
        }
        with patch("spectarr_agent.api.request.urlopen", side_effect=open_request):
            self.api.create_upload(
                "agt_secret",
                "stable-key",
                run_id=None,
                run={"experiment_id": "experiment-1", "name": "sample"},
                filename="sample.d",
                format_name="vendor_directory",
                total_size=None,
                sha256=None,
                bundle_manifest=manifest,
            )
        api_request = captured[0]
        payload = json.loads(api_request.data)
        self.assertEqual(payload["bundle_manifest"], manifest)
        self.assertNotIn("total_size", payload)
        self.assertNotIn("sha256", payload)
        self.assertEqual(api_request.get_header("Idempotency-key"), "stable-key")


if __name__ == "__main__":
    unittest.main()
