from __future__ import annotations

import hashlib
import hmac
import unittest
from urllib import error

from spectarr_webhooks.api import ClaimedDelivery
from spectarr_webhooks.delivery import WebhookSender, classify_status, signature_header, validate_destination


class FakeResponse:
    def __init__(self, status: int, content: bytes = b"") -> None:
        self.status = status
        self.content = content
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return self.content[:size]


def claim(url: str = "https://receiver.example/hooks") -> ClaimedDelivery:
    return ClaimedDelivery(
        id="delivery-1",
        url=url,
        body='{ "topic" : "artifact.ready", "payload" : {"id":1} }',
        headers={
            "X-Spectarr-Event": "artifact.ready",
            "Authorization": "must-not-pass",
            "Host": "must-not-pass",
        },
        signing_secret="whsec_secret",
        attempt=1,
        lease_expires_at="2099-01-01T00:00:00+00:00",
    )


class DeliveryTests(unittest.TestCase):
    def test_signature_covers_timestamp_dot_and_exact_body(self) -> None:
        body = b'{ "a": 1 }'
        expected = hmac.new(b"whsec_secret", b"1700000000." + body, hashlib.sha256).hexdigest()
        self.assertEqual(
            signature_header("whsec_secret", 1_700_000_000, body),
            f"t=1700000000,v1={expected}",
        )

    def test_sender_preserves_body_and_filters_untrusted_headers(self) -> None:
        captured = []
        response = FakeResponse(204)

        def open_request(value, timeout):
            captured.append((value, timeout))
            return response

        outcome = WebhookSender(opener=open_request, max_response_bytes=1024).send(claim(), 1_700_000_000)
        self.assertEqual(outcome.status, "delivered")
        outbound, timeout = captured[0]
        self.assertEqual(outbound.data, claim().body.encode())
        self.assertIsNone(outbound.get_header("Authorization"))
        self.assertIsNone(outbound.get_header("Host"))
        self.assertEqual(outbound.get_header("X-spectarr-delivery"), "delivery-1")
        self.assertEqual(outbound.get_header("X-spectarr-timestamp"), "1700000000")
        self.assertEqual(response.read_sizes, [1025])
        self.assertEqual(timeout, 15)

    def test_rejects_unsafe_schemes_userinfo_fragments_and_http_by_default(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.test/hook",
            "http://example.test/hook",
            "https://user:pass@example.test/hook",
            "https://example.test/hook#fragment",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    validate_destination(url, allow_http=False)
        validate_destination("http://localhost:8000/hook", allow_http=True)

    def test_network_failures_and_retryable_statuses_retry(self) -> None:
        def offline(value, timeout):
            raise error.URLError("offline")

        outcome = WebhookSender(opener=offline).send(claim(), 1)
        self.assertEqual(outcome.status, "retry")
        for status in (408, 425, 429, 500, 503):
            self.assertEqual(classify_status(status).status, "retry")

    def test_redirects_and_permanent_client_failures_are_terminal(self) -> None:
        for status in (301, 302, 400, 401, 403, 404, 409, 410, 413, 422):
            self.assertEqual(classify_status(status).status, "failed")

    def test_invalid_claim_body_is_never_sent(self) -> None:
        invalid = claim()
        invalid = ClaimedDelivery(**{**invalid.__dict__, "body": "not-json"})
        called = []
        outcome = WebhookSender(opener=lambda *args, **kwargs: called.append(args)).send(invalid, 1)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
