"""API-backed worker loop for queued Spectarr conversion jobs."""

from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import shutil
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Protocol
from urllib import error, parse, request

from . import __version__
from .models import ConversionRequest, ConversionResult
from .service import ConversionService, PINNED_DEFAULT_IMAGE


class WorkerApi(Protocol):
    def get(self, path: str, query: dict[str, Any] | None = None) -> Any: ...
    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any: ...
    def patch(self, path: str, payload: dict[str, Any]) -> Any: ...
    def upload_artifact(self, path: str, file_path: Path, fields: dict[str, str]) -> Any: ...


class HttpWorkerApi:
    """HTTP client with streaming multipart uploads for large derivatives."""

    def __init__(
        self,
        base_url: str,
        worker_token: str | None = None,
        timeout_seconds: float = 60.0,
        worker_id: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.worker_token = worker_token
        self.timeout_seconds = timeout_seconds
        self.worker_id = worker_id

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        return self._json_request("GET", path, query=query)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        return self._json_request("POST", path, payload=payload)

    def patch(self, path: str, payload: dict[str, Any]) -> Any:
        return self._json_request("PATCH", path, payload=payload)

    def _json_request(
        self,
        method: str,
        path: str,
        query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{parse.urlencode({key: value for key, value in query.items() if value is not None})}"
        body = json.dumps(payload).encode() if payload is not None else None
        headers = {"Accept": "application/json", "User-Agent": f"spectarr-converter/{__version__}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if self.worker_token:
            headers["X-Spectarr-Worker-Token"] = self.worker_token
        if self.worker_id:
            headers["X-Spectarr-Worker-Id"] = self.worker_id
        api_request = request.Request(url, data=body, headers=headers, method=method)
        try:
            with request.urlopen(api_request, timeout=self.timeout_seconds) as response:
                content = response.read()
        except error.HTTPError as api_error:
            detail = api_error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Spectarr API returned {api_error.code}: {detail}") from api_error
        except error.URLError as api_error:
            raise RuntimeError(f"Spectarr API is unavailable: {api_error.reason}") from api_error
        return json.loads(content) if content else None

    def upload_artifact(self, path: str, file_path: Path, fields: dict[str, str]) -> Any:
        boundary = f"spectarr-{uuid.uuid4().hex}"
        parameter_separator = chr(59)
        field_parts = [
            (
                f"--{boundary}\r\n"
                f"Content-Disposition: form-data{parameter_separator} name=\"{name}\"\r\n\r\n"
                f"{value}\r\n"
            ).encode()
            for name, value in fields.items()
        ]
        mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        file_header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data{parameter_separator} name=\"file\""
            f"{parameter_separator} filename=\"{file_path.name}\"\r\n"
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode()
        closing = f"\r\n--{boundary}--\r\n".encode()
        length = sum(map(len, field_parts)) + len(file_header) + file_path.stat().st_size + len(closing)
        parsed = parse.urlparse(self.base_url)
        connection_class = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        connection = connection_class(parsed.hostname, parsed.port, timeout=self.timeout_seconds)
        target = f"{parsed.path.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Content-Length": str(length),
            "Content-Type": f"multipart/form-data{parameter_separator} boundary={boundary}",
            "User-Agent": f"spectarr-converter/{__version__}",
        }
        if self.worker_token:
            headers["X-Spectarr-Worker-Token"] = self.worker_token
        if self.worker_id:
            headers["X-Spectarr-Worker-Id"] = self.worker_id
        try:
            connection.putrequest("POST", target)
            for name, value in headers.items():
                if value:
                    connection.putheader(name, value)
            connection.endheaders()
            for part in field_parts:
                connection.send(part)
            connection.send(file_header)
            with file_path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    connection.send(chunk)
            connection.send(closing)
            response = connection.getresponse()
            content = response.read()
            if response.status >= 400:
                raise RuntimeError(f"Artifact upload returned {response.status}: {content.decode(errors='replace')}")
            return json.loads(content) if content else None
        finally:
            connection.close()


class ApiConversionWorker:
    """Claim queued jobs and register validated outputs through the REST API."""

    def __init__(
        self,
        api: WorkerApi,
        converter: ConversionService,
        worker_id: str | None = None,
        local_storage_root: Path | None = None,
        heartbeat_seconds: float = 60.0,
    ) -> None:
        self.api = api
        self.converter = converter
        self.worker_id = worker_id or socket.gethostname()
        self.local_storage_root = local_storage_root.resolve() if local_storage_root else None
        self.heartbeat_seconds = max(1.0, heartbeat_seconds)

    def _convert_with_heartbeat(self, job_id: str, request_value: ConversionRequest) -> ConversionResult:
        stopped = threading.Event()
        cancelled = threading.Event()
        heartbeat_errors: list[Exception] = []
        last_progress = 0.1

        def heartbeat() -> None:
            while not stopped.wait(self.heartbeat_seconds):
                try:
                    current = self.api.get(f"/api/v1/jobs/{job_id}")
                    if current.get("state") == "cancelled":
                        cancelled.set()
                        stopped.set()
                        return
                    self.api.post(f"/api/v1/jobs/{job_id}/heartbeat")
                except Exception as error:
                    heartbeat_errors.append(error)
                    stopped.set()

        def report_progress(_phase: str, converter_progress: float) -> None:
            nonlocal last_progress
            mapped = min(0.85, 0.1 + max(0.0, min(converter_progress, 1.0)) * 0.75)
            if mapped < last_progress + 0.02 and mapped < 0.85:
                return
            self.api.patch(f"/api/v1/jobs/{job_id}", {"progress": mapped})
            last_progress = mapped

        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        try:
            result = self.converter.convert_with_control(request_value, cancelled, report_progress)
        finally:
            stopped.set()
            heartbeat_thread.join(timeout=min(self.heartbeat_seconds, 5.0))
        if heartbeat_errors and not cancelled.is_set():
            raise RuntimeError(f"Job heartbeat failed: {heartbeat_errors[0]}")
        return result

    def _resolve_worker_source(self, location: dict[str, Any]) -> str:
        if self.local_storage_root is None:
            return str(location["path"])
        relative_value = location.get("relative_path")
        if not relative_value:
            raise ValueError("Artifact location did not include a storage-relative path")
        source = (self.local_storage_root / str(relative_value)).resolve()
        if not source.is_relative_to(self.local_storage_root):
            raise ValueError("Artifact location escapes the local storage root")
        return str(source)

    def process_one(self) -> bool:
        jobs = self.api.get("/api/v1/jobs", {"state": "queued", "kind": "convert", "limit": 20})
        job = next((candidate for candidate in jobs if candidate.get("kind") == "convert"), None)
        if job is None:
            return False
        job_id = str(job["id"])
        try:
            claimed = self.api.post(f"/api/v1/jobs/{job_id}/claim")
        except RuntimeError as claim_error:
            if "409" in str(claim_error):
                return True
            raise
        attempts = int(claimed.get("attempts", 1))
        try:
            artifact_id = str(claimed["input_artifact_id"])
            recipe_id = str(claimed["recipe_id"])
            artifact = self.api.get(f"/api/v1/artifacts/{artifact_id}")
            location = self.api.get(f"/api/v1/artifacts/{artifact_id}/location")
            recipe = self.api.get(f"/api/v1/recipes/{recipe_id}")
            overrides = dict(claimed.get("parameters") or {})
            recipe_definition = overrides.pop("recipe_snapshot", None) or recipe
            recipe_revision = overrides.pop("recipe_revision", recipe_definition.get("revision", 1))
            overrides.pop("force_nonce", None)
            overrides.pop("recipe_fingerprint", None)
            if recipe_definition.get("converter") != "msconvert":
                raise ValueError("Only the msconvert converter is supported")
            if recipe_definition.get("converter_version") and not self.converter.image.endswith(
                f":{recipe_definition['converter_version']}"
            ):
                raise ValueError("Recipe converter version does not match the pinned worker image")
            request_value = ConversionRequest(
                job_id=f"{job_id}.attempt-{attempts}",
                source_path=self._resolve_worker_source(location),
                recipe=str(recipe_definition["name"]),
                source_name=str(location["filename"]),
                recipe_definition=recipe_definition,
                parameter_overrides=overrides,
            )
            self.api.patch(f"/api/v1/jobs/{job_id}", {"progress": 0.1})
            result = self._convert_with_heartbeat(job_id, request_value)
            if result.status != "succeeded" or not result.outputs:
                raise RuntimeError(result.error or "Conversion produced no validated output")
            if len(result.outputs) != 1:
                raise RuntimeError("A conversion job must produce exactly one output artifact")
            output = result.outputs[0]
            fingerprint = str(claimed.get("parameters", {}).get("recipe_fingerprint", ""))
            fields = {
                "role": "derived",
                "format": output.format,
                "parent_artifact_id": artifact_id,
                "recipe_id": recipe_id,
                "expected_sha256": output.sha256,
                "metadata_json": json.dumps(
                    {
                        "converter_image": result.image,
                        "converter_library_version": result.converter_version,
                        "converter_command": result.command,
                        "duration_seconds": result.duration_seconds,
                        "arguments": result.arguments,
                        "recipe_name": recipe_definition["name"],
                        "recipe_revision": recipe_revision,
                        "recipe_snapshot": recipe_definition,
                        "container_format": (
                            "pymzml-indexed-gzip" if output.path.lower().endswith(".mzml.gz") else "plain"
                        ),
                        "stdout": result.stdout[-32768:],
                        "stderr": result.stderr[-32768:],
                    },
                    separators=(",", ":"),
                ),
            }
            if fingerprint:
                fields["recipe_fingerprint"] = fingerprint
            uploaded = self.api.upload_artifact(
                f"/api/v1/runs/{artifact['run_id']}/artifacts/upload",
                Path(output.path),
                fields,
            )
            self.api.patch(
                f"/api/v1/jobs/{job_id}",
                {"state": "succeeded", "progress": 1.0, "output_artifact_id": uploaded["id"]},
            )
            if result.scratch_path:
                shutil.rmtree(result.scratch_path, ignore_errors=True)
        except Exception as conversion_error:
            current = self.api.get(f"/api/v1/jobs/{job_id}")
            if current.get("state") != "cancelled":
                self.api.patch(
                    f"/api/v1/jobs/{job_id}",
                    {"state": "failed", "error": str(conversion_error)[:10000]},
                )
        return True

    def run_forever(self, poll_seconds: float = 3.0) -> None:
        consecutive_api_errors = 0
        while True:
            try:
                handled = self.process_one()
            except RuntimeError as worker_error:
                if "Spectarr API is unavailable" not in str(worker_error):
                    raise
                consecutive_api_errors += 1
                if consecutive_api_errors == 1 or consecutive_api_errors % 10 == 0:
                    print(
                        f"Spectarr API unavailable, retrying: {worker_error}",
                        file=sys.stderr,
                        flush=True,
                    )
                retry_seconds = min(
                    max(poll_seconds, 1.0) * (2 ** min(consecutive_api_errors - 1, 5)),
                    60.0,
                )
                time.sleep(retry_seconds)
                continue
            if consecutive_api_errors:
                print("Spectarr API connection restored", file=sys.stderr, flush=True)
                consecutive_api_errors = 0
            if not handled:
                time.sleep(poll_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Spectarr API conversion worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    args = parser.parse_args()
    roots = tuple(Path(value) for value in os.getenv("SPECTARR_SOURCE_ROOTS", "/data").split(os.pathsep))
    worker_id = os.getenv("SPECTARR_WORKER_ID") or socket.gethostname()
    api = HttpWorkerApi(
        os.getenv("SPECTARR_API_URL", os.getenv("SPECTARR_URL", "http://api:8000")),
        os.getenv("SPECTARR_WORKER_TOKEN"),
        float(os.getenv("SPECTARR_API_TIMEOUT", "60")),
        worker_id,
    )
    converter = ConversionService(
        Path(os.getenv("SPECTARR_SCRATCH_ROOT", "/var/lib/spectarr/scratch")),
        roots,
        os.getenv("SPECTARR_MSCONVERT_IMAGE", PINNED_DEFAULT_IMAGE),
    )
    local_storage_value = os.getenv("SPECTARR_LOCAL_STORAGE_ROOT")
    worker = ApiConversionWorker(
        api,
        converter,
        worker_id,
        Path(local_storage_value) if local_storage_value else None,
        float(os.getenv("SPECTARR_HEARTBEAT_SECONDS", "60")),
    )
    if args.once:
        worker.process_one()
    else:
        worker.run_forever(max(0.1, min(args.poll_seconds, 60.0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
