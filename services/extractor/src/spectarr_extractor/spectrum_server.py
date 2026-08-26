"""Small authenticated HTTP service for interactive spectrum access."""

from __future__ import annotations

import argparse
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .spectra import SpectrumAccessError, SpxtacularSpectrumSource


class SpectrumHttpServer(ThreadingHTTPServer):
    source: SpxtacularSpectrumSource
    worker_token: str


class SpectrumRequestHandler(BaseHTTPRequestHandler):
    server: SpectrumHttpServer

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json_response(200, {"status": "ok"})
            return
        self._json_response(404, {"detail": "Not found"})

    def do_POST(self) -> None:
        if self.path not in {"/v1/spectra", "/v1/spectra/catalog"}:
            self._json_response(404, {"detail": "Not found"})
            return
        supplied_token = self.headers.get("X-Spectarr-Worker-Token", "")
        try:
            payload = self._request_json()
            if self.path == "/v1/spectra/catalog":
                result = process_catalog_request(
                    self.server.source,
                    self.server.worker_token,
                    supplied_token,
                    payload,
                )
            else:
                result = process_spectrum_request(
                    self.server.source,
                    self.server.worker_token,
                    supplied_token,
                    payload,
                )
        except SpectrumAccessError as error:
            self._json_response(error.status, {"detail": str(error)})
            return
        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
            UnicodeDecodeError,
        ) as error:
            self._json_response(400, {"detail": str(error)})
            return
        self._json_response(200, result)

    def _request_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length must be an integer") from error
        if length <= 0 or length > 65536:
            raise ValueError("Request body must contain between 1 and 65536 bytes")
        value = json.loads(self.rfile.read(length))
        if not isinstance(value, dict):
            raise TypeError("Request body must be a JSON object")
        return value

    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"spectrum-reader: {format % args}")


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _integer(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if type(value) is not int:
        raise TypeError(f"{key} must be an integer")
    return value


def _optional_integer(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is not None and type(value) is not int:
        raise TypeError(f"{key} must be an integer or null")
    return value


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value


def _optional_float(payload: dict[str, Any], key: str) -> float | None:
    value = payload.get(key)
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, (int, float))
    ):
        raise TypeError(f"{key} must be a number or null")
    return float(value) if value is not None else None


def process_spectrum_request(
    source: SpxtacularSpectrumSource,
    worker_token: str,
    supplied_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Authenticate and validate one decoded spectrum request."""
    if not worker_token or not hmac.compare_digest(supplied_token, worker_token):
        raise SpectrumAccessError(401, "Valid worker token required")
    return source.read(
        _string(payload, "relative_path"),
        ms_level=_integer(payload, "ms_level"),
        index=_optional_integer(payload, "index"),
        scan_number=_optional_integer(payload, "scan_number"),
        native_id=_optional_string(payload, "native_id"),
    )


def process_catalog_request(
    source: SpxtacularSpectrumSource,
    worker_token: str,
    supplied_token: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Authenticate and validate a spectrum catalog request."""
    if not worker_token or not hmac.compare_digest(supplied_token, worker_token):
        raise SpectrumAccessError(401, "Valid worker token required")
    return source.browse(
        _string(payload, "relative_path"),
        ms_level=_integer(payload, "ms_level"),
        offset=_integer(payload, "offset"),
        limit=_integer(payload, "limit"),
        rt_seconds=_optional_float(payload, "rt_seconds"),
        scan_number=_optional_integer(payload, "scan_number"),
        native_id=_optional_string(payload, "native_id"),
        precursor_mz=_optional_float(payload, "precursor_mz"),
    )


def create_server(
    host: str, port: int, storage_root: Path, worker_token: str
) -> SpectrumHttpServer:
    server = SpectrumHttpServer((host, port), SpectrumRequestHandler)
    server.source = SpxtacularSpectrumSource(storage_root)
    server.worker_token = worker_token
    return server


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serve Spxtacular spectra to the Spectarr API"
    )
    parser.add_argument(
        "--host", default=os.getenv("SPECTARR_SPECTRUM_HOST", "0.0.0.0")
    )
    parser.add_argument(
        "--port", type=int, default=int(os.getenv("SPECTARR_SPECTRUM_PORT", "8002"))
    )
    args = parser.parse_args()
    token = os.getenv("SPECTARR_WORKER_TOKEN", "")
    if not token:
        parser.error("SPECTARR_WORKER_TOKEN is required")
    server = create_server(
        args.host,
        args.port,
        Path(os.getenv("SPECTARR_LOCAL_STORAGE_ROOT", "/data/storage")),
        token,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
