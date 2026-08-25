"""Standard-library client for the Spectarr agent and upload API."""

from __future__ import annotations

import json
import platform
import socket
from dataclasses import dataclass
from typing import Any
from urllib import error, parse, request

from . import __version__


class ApiError(RuntimeError):
    """A normalized API failure with resumable upload metadata."""

    def __init__(self, status: int, detail: str, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self.detail = detail
        self.headers = headers or {}
        super().__init__(f"Spectarr API returned {status}: {detail}")

    @property
    def retryable(self) -> bool:
        return self.status == 0 or self.status == 408 or self.status == 429 or self.status >= 500

    @property
    def expected_offset(self) -> int | None:
        value = self.headers.get("upload-offset")
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None


@dataclass(frozen=True)
class AgentRegistration:
    id: str
    token: str


class SpectarrAgentApi:
    """Call the public API without reading Spectarr storage or its database."""

    def __init__(self, base_url: str, timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def register(self, admin_token: str, name: str, local_agent_id: str) -> AgentRegistration:
        response, _ = self._request(
            "POST",
            "/api/v1/agents/register",
            token=admin_token,
            json_body={
                "name": name,
                "version": __version__,
                "capabilities": ["resumable_upload", "bundle_upload", "offline_queue", "polling"],
                "metadata_json": {
                    "local_agent_id": local_agent_id,
                    "hostname": socket.gethostname(),
                    "platform": platform.system().lower(),
                    "python": platform.python_version(),
                },
            },
        )
        token = str(response.get("token", ""))
        if not token.startswith("agt_"):
            raise ApiError(502, "Registration response did not include a valid one-time agent token")
        return AgentRegistration(str(response["id"]), token)

    def heartbeat(self, agent_id: str, agent_token: str, status: str, capacity: dict[str, Any]) -> dict:
        response, _ = self._request(
            "POST",
            f"/api/v1/agents/{parse.quote(agent_id, safe='')}/heartbeat",
            token=agent_token,
            json_body={
                "status": status,
                "capacity": capacity,
                "metadata_json": {
                    "version": __version__,
                    "hostname": socket.gethostname(),
                    "platform": platform.system().lower(),
                    "python": platform.python_version(),
                },
            },
        )
        return response

    def create_upload(
        self,
        agent_token: str,
        idempotency_key: str,
        *,
        run_id: str | None,
        run: dict[str, Any] | None,
        filename: str,
        format_name: str,
        total_size: int | None,
        sha256: str | None,
        bundle_manifest: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict:
        response, _ = self._request(
            "POST",
            "/api/v1/upload-sessions",
            token=agent_token,
            headers={"Idempotency-Key": idempotency_key},
            json_body={key: value for key, value in {
                "run_id": run_id,
                "run": run,
                "filename": filename,
                "format": format_name,
                "role": "source",
                "total_size": total_size,
                "sha256": sha256,
                "bundle_manifest": bundle_manifest,
                "metadata_json": metadata or {},
            }.items() if value is not None},
        )
        return response

    def get_upload(self, agent_token: str, upload_id: str) -> dict:
        response, _ = self._request(
            "GET",
            f"/api/v1/upload-sessions/{parse.quote(upload_id, safe='')}",
            token=agent_token,
        )
        return response

    def upload_chunk(self, agent_token: str, upload_id: str, offset: int, content: bytes) -> int:
        return self._upload_chunk_to_path(agent_token, upload_id, None, offset, content)

    def upload_bundle_chunk(
        self,
        agent_token: str,
        upload_id: str,
        relative_path: str,
        offset: int,
        content: bytes,
    ) -> int:
        return self._upload_chunk_to_path(agent_token, upload_id, relative_path, offset, content)

    def _upload_chunk_to_path(
        self,
        agent_token: str,
        upload_id: str,
        relative_path: str | None,
        offset: int,
        content: bytes,
    ) -> int:
        suffix = ""
        if relative_path is not None:
            suffix = f"/files/{parse.quote(relative_path, safe='/')}"
        _, headers = self._request(
            "PATCH",
            f"/api/v1/upload-sessions/{parse.quote(upload_id, safe='')}{suffix}",
            token=agent_token,
            headers={
                "Content-Type": "application/octet-stream",
                "Upload-Offset": str(offset),
                "Idempotency-Key": f"{upload_id}:{offset}:{len(content)}",
            },
            raw_body=content,
            expected_empty=True,
        )
        raw_offset = headers.get("upload-offset")
        if raw_offset is None:
            raise ApiError(502, "Chunk response omitted Upload-Offset", headers)
        try:
            return int(raw_offset)
        except ValueError as decode_error:
            raise ApiError(502, "Chunk response contained an invalid Upload-Offset", headers) from decode_error

    def complete_upload(self, agent_token: str, upload_id: str) -> dict:
        response, _ = self._request(
            "POST",
            f"/api/v1/upload-sessions/{parse.quote(upload_id, safe='')}/complete",
            token=agent_token,
            json_body={},
        )
        return response

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        raw_body: bytes | None = None,
        expected_empty: bool = False,
    ) -> tuple[dict, dict[str, str]]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        body = raw_body
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": f"spectarr-agent/{__version__}",
            **(headers or {}),
        }
        if json_body is not None:
            body = json.dumps(json_body, separators=(",", ":")).encode()
            request_headers["Content-Type"] = "application/json"
        api_request = request.Request(url, data=body, headers=request_headers, method=method)
        try:
            with request.urlopen(api_request, timeout=self.timeout_seconds) as response:
                content = response.read()
                response_headers = {key.lower(): value for key, value in response.headers.items()}
        except error.HTTPError as api_error:
            content = api_error.read()
            response_headers = {key.lower(): value for key, value in api_error.headers.items()}
            detail = self._error_detail(content)
            raise ApiError(api_error.code, detail, response_headers) from api_error
        except (error.URLError, TimeoutError, OSError) as api_error:
            raise ApiError(0, str(getattr(api_error, "reason", api_error))) from api_error
        if not content:
            if expected_empty:
                return {}, response_headers
            return {}, response_headers
        try:
            value = json.loads(content)
        except json.JSONDecodeError as decode_error:
            raise ApiError(502, "Spectarr API returned invalid JSON", response_headers) from decode_error
        if not isinstance(value, dict):
            raise ApiError(502, "Spectarr API returned a non-object response", response_headers)
        return value, response_headers

    @staticmethod
    def _error_detail(content: bytes) -> str:
        try:
            value = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return content.decode("utf-8", errors="replace")[:10000]
        if isinstance(value, dict):
            return str(value.get("detail", value))[:10000]
        return str(value)[:10000]
