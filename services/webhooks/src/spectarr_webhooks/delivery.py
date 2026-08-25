"""Safe webhook signing, transport, and response classification."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Callable
from urllib import error, parse, request

from .api import ClaimedDelivery


class UnsafeDestination(ValueError):
    """A webhook URL violates the worker transport policy."""


@dataclass(frozen=True)
class DeliveryOutcome:
    status: str
    response_status: int | None = None
    error: str | None = None


class NoRedirectHandler(request.HTTPRedirectHandler):
    """Never follow destination redirects implicitly."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class WebhookSender:
    """Transmit a claimed body exactly once during the current lease."""

    SAFE_CLAIM_HEADERS = {"content-type", "x-spectarr-event", "x-spectarr-delivery"}

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        max_response_bytes: int = 64 * 1024,
        allow_http: bool = False,
        opener: Callable | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_response_bytes = max_response_bytes
        self.allow_http = allow_http
        self.opener = opener or request.build_opener(NoRedirectHandler()).open

    def send(self, delivery: ClaimedDelivery, timestamp: int) -> DeliveryOutcome:
        try:
            validate_destination(delivery.url, self.allow_http)
            body = delivery.body.encode("utf-8")
            json.loads(body)
            headers = self._headers(delivery, timestamp, body)
            outbound = request.Request(delivery.url, data=body, headers=headers, method="POST")
            try:
                with self.opener(outbound, timeout=self.timeout_seconds) as response:
                    status = int(response.status)
                    response_body = response.read(self.max_response_bytes + 1)
            except error.HTTPError as http_error:
                status = int(http_error.code)
                response_body = http_error.read(self.max_response_bytes + 1)
            detail = response_summary(response_body, self.max_response_bytes)
            return classify_status(status, detail)
        except UnsafeDestination as unsafe:
            return DeliveryOutcome("failed", error=str(unsafe))
        except (json.JSONDecodeError, UnicodeEncodeError) as invalid:
            return DeliveryOutcome("failed", error=f"Invalid canonical JSON body: {invalid}")
        except (error.URLError, TimeoutError, OSError) as transport_error:
            reason = str(getattr(transport_error, "reason", transport_error))
            return DeliveryOutcome("retry", error=f"Webhook transport error: {reason}"[:10000])

    def _headers(self, delivery: ClaimedDelivery, timestamp: int, body: bytes) -> dict[str, str]:
        headers = {
            key: value
            for key, value in delivery.headers.items()
            if key.casefold() in self.SAFE_CLAIM_HEADERS
        }
        headers["Content-Type"] = "application/json"
        headers["X-Spectarr-Delivery"] = delivery.id
        headers["X-Spectarr-Timestamp"] = str(timestamp)
        headers["X-Spectarr-Signature"] = signature_header(delivery.signing_secret, timestamp, body)
        headers["User-Agent"] = "Spectarr-Webhook/1.0"
        return headers


def validate_destination(url: str, allow_http: bool) -> None:
    if not url or any(character in url for character in ("\r", "\n", "\x00")):
        raise UnsafeDestination("Webhook URL contains invalid characters")
    parsed = parse.urlsplit(url)
    allowed = {"https"} | ({"http"} if allow_http else set())
    if parsed.scheme.casefold() not in allowed:
        policy = "https or explicitly enabled http" if allow_http else "https"
        raise UnsafeDestination(f"Webhook URL must use {policy}")
    if not parsed.hostname:
        raise UnsafeDestination("Webhook URL must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise UnsafeDestination("Webhook URL cannot contain user information")
    if parsed.fragment:
        raise UnsafeDestination("Webhook URL cannot contain a fragment")
    try:
        port = parsed.port
    except ValueError as invalid_port:
        raise UnsafeDestination("Webhook URL contains an invalid port") from invalid_port
    if port is not None and not 1 <= port <= 65535:
        raise UnsafeDestination("Webhook URL contains an invalid port")


def signature_header(secret: str, timestamp: int, body: bytes) -> str:
    signed = str(timestamp).encode("ascii") + b"." + body
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={digest}"


def classify_status(status: int, detail: str | None = None) -> DeliveryOutcome:
    if 200 <= status < 300:
        return DeliveryOutcome("delivered", response_status=status)
    message = f"Destination returned HTTP {status}"
    if detail:
        message = f"{message}: {detail}"
    retryable = status in {408, 425, 429} or 500 <= status < 600
    return DeliveryOutcome("retry" if retryable else "failed", response_status=status, error=message[:10000])


def response_summary(content: bytes, limit: int) -> str | None:
    if not content:
        return None
    truncated = len(content) > limit
    text = content[:limit].decode("utf-8", errors="replace")
    if truncated:
        text = f"{text} [truncated]"
    return " ".join(text.split())[:4096]
