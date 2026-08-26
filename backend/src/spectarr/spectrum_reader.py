"""Client for the internal Spxtacular spectrum-reader service."""

from __future__ import annotations

import json
from typing import Any

import httpx


class SpectrumReaderError(RuntimeError):
    def __init__(self, status: int, detail: str) -> None:
        self.status = status
        super().__init__(detail)


class SpectrumReaderClient:
    """Fetch versioned Spxtacular spectrum payloads over the private network."""

    maximum_response_bytes = 128 * 1024 * 1024

    def __init__(
        self,
        base_url: str | None,
        worker_token: str | None,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") if base_url else None
        self.worker_token = worker_token
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("/v1/spectra", payload, "spxtacular.spectrum")

    async def catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request(
            "/v1/spectra/catalog", payload, "spectarr.spectrum-catalog"
        )

    async def _request(
        self, path: str, payload: dict[str, Any], expected_schema: str
    ) -> dict[str, Any]:
        if not self.base_url:
            raise SpectrumReaderError(503, "Spectrum reader is not configured")
        if not self.worker_token:
            raise SpectrumReaderError(
                503, "Spectrum reader authentication is not configured"
            )
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Spectarr-Worker-Token": self.worker_token,
        }
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self.timeout_seconds, transport=self.transport
                ) as client,
                client.stream(
                    "POST",
                    f"{self.base_url}{path}",
                    content=body,
                    headers=headers,
                ) as response,
            ):
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > self.maximum_response_bytes:
                        raise SpectrumReaderError(
                            502,
                            "Spectrum reader response exceeded the configured safety limit",
                        )
        except httpx.TimeoutException as api_error:
            raise SpectrumReaderError(504, "Spectrum reader timed out") from api_error
        except httpx.RequestError as api_error:
            raise SpectrumReaderError(
                503, f"Spectrum reader is unavailable: {api_error}"
            ) from api_error
        if response.status_code >= 400:
            detail = _error_detail(
                content, f"Spectrum reader returned status {response.status_code}"
            )
            raise SpectrumReaderError(response.status_code, detail)
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as decode_error:
            raise SpectrumReaderError(
                502, "Spectrum reader returned invalid JSON"
            ) from decode_error
        if not isinstance(value, dict):
            raise SpectrumReaderError(
                502, "Spectrum reader returned a non-object response"
            )
        if value.get("schema") != expected_schema or value.get("schema_version") != 1:
            raise SpectrumReaderError(
                502, "Spectrum reader returned an unsupported transport schema"
            )
        return value


def _error_detail(content: bytes | bytearray, fallback: str) -> str:
    try:
        value = json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return fallback
    return str(value.get("detail", fallback)) if isinstance(value, dict) else fallback
