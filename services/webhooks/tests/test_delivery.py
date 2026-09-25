from __future__ import annotations

import hashlib
import hmac
import unittest
from urllib import error, request
from unittest.mock import MagicMock, patch
from http.client import BadStatusLine

from spectarr_webhooks.api import ClaimedDelivery
from spectarr_webhooks.delivery import (
    WebhookSender,
    open_pinned,
    classify_status,
    signature_header,
    validate_destination,
)


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


def public_resolver(*args, **kwargs):
    return [(2, 1, 6, "", ("8.8.8.8", 443))]


class DeliveryTests(unittest.TestCase):
    def test_malformed_http_response_is_retryable(self):
        with patch('spectarr_webhooks.delivery.open_pinned', side_effect=BadStatusLine('invalid')):
            outcome = WebhookSender(resolver=public_resolver).send(claim(), 1)
        self.assertEqual(outcome.status, 'retry')

    def test_transport_uses_checked_ip_and_original_tls_hostname(self):
        connection = MagicMock()
        response = connection.getresponse.return_value.__enter__.return_value
        checked_socket = MagicMock()
        with patch('spectarr_webhooks.delivery.HTTPSConnection', return_value=connection), patch('spectarr_webhooks.delivery.socket.create_connection', return_value=checked_socket) as connect, patch('spectarr_webhooks.delivery.ssl.create_default_context') as context:
            outbound = request.Request('https://receiver.example/hooks?x=1', data=b'{}', method='POST')
            with open_pinned(outbound, ('8.8.8.8',), 3) as result:
                self.assertIs(result, response)
            connect.assert_called_once_with(('8.8.8.8', 443), timeout=3)
            context.return_value.wrap_socket.assert_called_once_with(checked_socket, server_hostname='receiver.example')
            connection.request.assert_called_once_with('POST', '/hooks?x=1', body=b'{}', headers={})
            connection.close.assert_called_once()

    def test_dns_is_resolved_once_and_checked_addresses_reach_transport(self):
        resolver = MagicMock(side_effect=[public_resolver(), [(2, 1, 6, '', ('127.0.0.1', 443))]])
        with patch('spectarr_webhooks.delivery.open_pinned') as transport:
            transport.return_value.__enter__.return_value = FakeResponse(204)
            outcome = WebhookSender(resolver=resolver).send(claim(), 1)
            self.assertEqual(outcome.status, 'delivered')
            resolver.assert_called_once()
            self.assertEqual(transport.call_args.args[1], ('8.8.8.8',))

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

        outcome = WebhookSender(
            opener=open_request,
            resolver=public_resolver,
            max_response_bytes=1024,
        ).send(claim(), 1_700_000_000)
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
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_destination(url, allow_http=False)
        validate_destination(
            "http://localhost:8000/hook",
            allow_http=True,
            allow_private_networks=True,
        )

    def test_rejects_private_and_mixed_public_private_resolution(self) -> None:
        private = lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))]
        mixed = lambda *args, **kwargs: [
            (2, 1, 6, "", ("8.8.8.8", 443)),
            (2, 1, 6, "", ("10.0.0.5", 443)),
        ]
        multicast = lambda *args, **kwargs: [(2, 1, 6, "", ("224.0.0.1", 443))]
        for resolver in (private, mixed, multicast):
            with self.subTest(resolver=resolver), self.assertRaisesRegex(
                ValueError, "private"
            ):
                validate_destination(
                    "https://receiver.example/hooks",
                    allow_http=False,
                    resolver=resolver,
                )

    def test_network_failures_and_retryable_statuses_retry(self) -> None:
        def offline(value, timeout):
            raise error.URLError("offline")

        outcome = WebhookSender(opener=offline, resolver=public_resolver).send(claim(), 1)
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
        outcome = WebhookSender(
            opener=lambda *args, **kwargs: called.append(args),
            resolver=public_resolver,
        ).send(invalid, 1)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(called, [])


if __name__ == "__main__":
    unittest.main()
