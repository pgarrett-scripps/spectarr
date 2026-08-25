from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from spectarr_webhooks.api import SpectarrWebhookApi


class FakeResponse:
    def __init__(self, value) -> None:
        self.content = json.dumps(value).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return self.content


class ApiTests(unittest.TestCase):
    def test_service_bearer_lists_and_claims_with_worker_identity(self) -> None:
        captured = []
        responses = [
            FakeResponse([{"id": "delivery-1"}]),
            FakeResponse(
                {
                    "id": "delivery-1",
                    "url": "https://receiver.test/hooks",
                    "body": "{}",
                    "event": {"topic": "artifact.ready"},
                    "signing_secret": "whsec_secret",
                    "attempt": 1,
                    "lease_expires_at": "2099-01-01T00:00:00Z",
                }
            ),
        ]

        def open_request(value, timeout):
            captured.append(value)
            return responses.pop(0)

        api = SpectarrWebhookApi(
            "http://api:8000", "worker-1", service_token="service-token"
        )
        with patch("spectarr_webhooks.api.request.urlopen", side_effect=open_request):
            self.assertEqual(api.list_ready("pending", 25)[0]["id"], "delivery-1")
            claimed = api.claim("delivery-1")
        self.assertEqual(claimed.body, "{}")
        self.assertEqual(claimed.headers["X-Spectarr-Event"], "artifact.ready")
        self.assertIn("status_filter=pending", captured[0].full_url)
        self.assertEqual(captured[0].get_header("Authorization"), "Bearer service-token")
        self.assertEqual(captured[1].get_header("X-spectarr-worker-id"), "worker-1")

    def test_legacy_worker_header_and_patch_payload(self) -> None:
        captured = []

        def open_request(value, timeout):
            captured.append(value)
            return FakeResponse({"id": "delivery-1", "status": "retry"})

        api = SpectarrWebhookApi(
            "http://api:8000", "legacy-1", worker_token="legacy-token"
        )
        with patch("spectarr_webhooks.api.request.urlopen", side_effect=open_request):
            api.update(
                "delivery-1",
                "retry",
                response_status=503,
                error_detail="unavailable",
            )
        outbound = captured[0]
        self.assertEqual(outbound.get_header("X-spectarr-worker-token"), "legacy-token")
        self.assertEqual(outbound.get_header("X-spectarr-worker-id"), "legacy-1")
        self.assertEqual(json.loads(outbound.data)["response_status"], 503)


if __name__ == "__main__":
    unittest.main()
