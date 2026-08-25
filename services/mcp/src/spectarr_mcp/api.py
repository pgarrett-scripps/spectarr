"""Small standard-library client for the Spectarr REST API."""

from __future__ import annotations

import json
import http.client
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request


class SpectarrApiError(RuntimeError):
    """A normalized REST API failure."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


@dataclass
class SpectarrApiClient:
    """Call only the public API, never Spectarr storage or database internals."""

    base_url: str
    api_key: str | None = None
    worker_token: str | None = None
    timeout_seconds: float = 30.0

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self.request("GET", path, query=query)

    def post(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("POST", path, payload=payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self.request("PATCH", path, payload=payload)

    def upload_artifact(self, path: str, file_path: Path, fields: dict[str, str]) -> Any:
        """Stream a multipart artifact upload without buffering it in memory."""

        boundary = f"spectarr-{uuid.uuid4().hex}"
        parameter_separator = chr(59)
        parts = []
        for name, value in fields.items():
            parts.append(
                (
                    f"--{boundary}\r\n"
                    f"Content-Disposition: form-data{parameter_separator} name=\"{name}\"\r\n\r\n"
                    f"{value}\r\n"
                ).encode()
            )
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data{parameter_separator} name=\"file\""
            f"{parameter_separator} filename=\"{file_path.name}\"\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode()
        closing = f"\r\n--{boundary}--\r\n".encode()
        content_length = sum(len(part) for part in parts) + len(file_header) + file_path.stat().st_size + len(closing)
        parsed = parse.urlparse(self.base_url)
        connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, parsed.port, timeout=self.timeout_seconds)
        target = f"{parsed.path.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Content-Type": f"multipart/form-data{parameter_separator} boundary={boundary}",
            "Content-Length": str(content_length),
            "User-Agent": "spectarr-mcp/0.1",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.worker_token:
            headers["X-Spectarr-Worker-Token"] = self.worker_token
        try:
            connection.putrequest("POST", target)
            for name, value in headers.items():
                connection.putheader(name, value)
            connection.endheaders()
            for part in parts:
                connection.send(part)
            connection.send(file_header)
            with file_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    connection.send(chunk)
            connection.send(closing)
            response = connection.getresponse()
            content = response.read()
            if response.status >= 400:
                raise SpectarrApiError(response.status, content.decode("utf-8", errors="replace"))
            return json.loads(content) if content else None
        finally:
            connection.close()

    def request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        base = self.base_url.rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        if query:
            clean_query = {key: value for key, value in query.items() if value is not None}
            url = f"{url}?{parse.urlencode(clean_query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "User-Agent": "spectarr-mcp/0.1"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.worker_token:
            headers["X-Spectarr-Worker-Token"] = self.worker_token
        api_request = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(api_request, timeout=self.timeout_seconds) as response:
                content = response.read()
        except error.HTTPError as api_error:
            content = api_error.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(content).get("detail", content)
            except json.JSONDecodeError:
                detail = content
            raise SpectarrApiError(api_error.code, str(detail)) from api_error
        except error.URLError as api_error:
            raise SpectarrApiError(0, f"Spectarr API is unavailable: {api_error.reason}") from api_error
        if not content:
            return None
        try:
            return json.loads(content)
        except json.JSONDecodeError as decode_error:
            raise SpectarrApiError(502, "Spectarr API returned invalid JSON") from decode_error
