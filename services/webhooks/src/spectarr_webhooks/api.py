"""Standard-library client for the Spectarr webhook delivery queue."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from . import __version__


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Spectarr API returned {status}: {detail}")

    @property
    def retryable(self) -> bool:
        return self.status == 0 or self.status in {408, 425, 429} or self.status >= 500


@dataclass(frozen=True)
class ClaimedDelivery:
    id: str
    url: str
    body: str
    headers: dict[str, str]
    signing_secret: str
    attempt: int
    lease_expires_at: str

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ClaimedDelivery":
        body = value.get("body")
        if not isinstance(body, str):
            raise ValueError("Claim response body must be an exact JSON string")
        secret = str(value.get("signing_secret", ""))
        if not secret.startswith("whsec_"):
            raise ValueError("Claim response omitted a valid webhook signing secret")
        headers = {str(key): str(item) for key, item in dict(value.get("headers") or {}).items()}
        event = value.get("event")
        if isinstance(event, dict) and event.get("topic"):
            headers["X-Spectarr-Event"] = str(event["topic"])
        return cls(
            id=str(value["id"]),
            url=str(value["url"]),
            body=body,
            headers=headers,
            signing_secret=secret,
            attempt=int(value.get("attempt", 1)),
            lease_expires_at=str(value.get("lease_expires_at", "")),
        )


class SpectarrWebhookApi:
    """Poll and update deliveries using bearer or legacy worker authentication."""

    def __init__(
        self,
        base_url: str,
        worker_id: str,
        *,
        service_token: str | None = None,
        worker_token: str | None = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_id = worker_id
        self.service_token = service_token
        self.worker_token = worker_token
        self.timeout_seconds = timeout_seconds

    def list_ready(self, status: str, limit: int) -> list[dict[str, Any]]:
        if status not in {"pending", "retry"}:
            raise ValueError("Webhook poll status must be pending or retry")
        value = self._request(
            "GET",
            "/api/v1/webhook-deliveries",
            query={"status_filter": status, "limit": limit},
        )
        if not isinstance(value, list):
            raise ApiError(502, "Webhook delivery list was not an array")
        return [item for item in value if isinstance(item, dict)]

    def claim(self, delivery_id: str) -> ClaimedDelivery:
        value = self._request(
            "POST",
            f"/api/v1/webhook-deliveries/{parse.quote(delivery_id, safe='')}/claim",
            include_worker_id=True,
        )
        if not isinstance(value, dict):
            raise ApiError(502, "Webhook claim response was not an object")
        return ClaimedDelivery.from_dict(value)

    def update(
        self,
        delivery_id: str,
        status: str,
        *,
        response_status: int | None = None,
        error_detail: str | None = None,
    ) -> dict[str, Any]:
        if status not in {"delivered", "retry", "failed"}:
            raise ValueError("Invalid webhook delivery result")
        payload = {
            "status": status,
            "response_status": response_status,
            "error": error_detail[:10000] if error_detail else None,
        }
        value = self._request(
            "PATCH",
            f"/api/v1/webhook-deliveries/{parse.quote(delivery_id, safe='')}",
            json_body=payload,
            include_worker_id=True,
        )
        if not isinstance(value, dict):
            raise ApiError(502, "Webhook update response was not an object")
        return value

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        include_worker_id: bool = False,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{parse.urlencode(query)}"
        headers = {
            "Accept": "application/json",
            "User-Agent": f"spectarr-webhook-worker/{__version__}",
        }
        if self.service_token:
            headers["Authorization"] = f"Bearer {self.service_token}"
        elif self.worker_token:
            headers["X-Spectarr-Worker-Token"] = self.worker_token
        if include_worker_id:
            headers["X-Spectarr-Worker-Id"] = self.worker_id
        body = None
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        api_request = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(api_request, timeout=self.timeout_seconds) as response:
                content = response.read()
        except error.HTTPError as api_error:
            content = api_error.read()
            raise ApiError(api_error.code, decode_error(content)) from api_error
        except (error.URLError, TimeoutError, OSError) as api_error:
            raise ApiError(0, str(getattr(api_error, "reason", api_error))) from api_error
        if not content:
            return {}
        try:
            return json.loads(content)
        except json.JSONDecodeError as decode_error_value:
            raise ApiError(502, "Spectarr API returned invalid JSON") from decode_error_value


def decode_error(content: bytes) -> str:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return content.decode("utf-8", errors="replace")[:10000]
    return str(value.get("detail", value) if isinstance(value, dict) else value)[:10000]
