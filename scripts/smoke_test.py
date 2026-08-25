#!/usr/bin/env python3
"""Exercise a running Spectarr stack through its public HTTP boundary."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path
from urllib import error, request


BASE_URL = os.getenv("SPECTARR_SMOKE_URL", "http://127.0.0.1:3280/api/v1").rstrip("/")
USERNAME = os.getenv("SPECTARR_SMOKE_USERNAME", "release-admin")
PASSWORD = os.getenv("SPECTARR_SMOKE_PASSWORD", "release-rehearsal-admin-password")
FIXTURE = Path(os.getenv("SPECTARR_SMOKE_FIXTURE", Path(__file__).resolve().parents[1] / "examples/demo.mgf"))


def call(
    method: str,
    path: str,
    payload: object | None = None,
    token: str | None = None,
    content_type: str = "application/json",
    timeout: float = 30,
) -> tuple[int, bytes, dict[str, str]]:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = content_type
    if token:
        headers["Authorization"] = f"Bearer {token}"
    api_request = request.Request(f"{BASE_URL}/{path.lstrip('/')}", data=body, headers=headers, method=method)
    try:
        with request.urlopen(api_request, timeout=timeout) as response:
            return response.status, response.read(), dict(response.headers)
    except error.HTTPError as api_error:
        detail = api_error.read()
        raise RuntimeError(f"{method} {path} returned {api_error.code}: {detail.decode(errors='replace')}") from api_error


def json_call(method: str, path: str, payload: object | None = None, token: str | None = None) -> object:
    _status, body, _headers = call(method, path, payload, token)
    return json.loads(body) if body else None


def wait_until_ready(timeout_seconds: float = 180) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            status = json_call("GET", "/auth/bootstrap/status")
            if isinstance(status, dict) and "required" in status:
                return
        except Exception as readiness_error:
            last_error = str(readiness_error)
        time.sleep(2)
    raise RuntimeError(f"Stack did not become ready: {last_error}")


def authenticate() -> str:
    status = json_call("GET", "/auth/bootstrap/status")
    endpoint = "/auth/bootstrap" if status == {"required": True} else "/auth/login"
    response = json_call("POST", endpoint, {"username": USERNAME, "password": PASSWORD})
    if not isinstance(response, dict) or not response.get("access_token"):
        raise RuntimeError("Authentication response did not include an access token")
    return str(response["access_token"])


def upload(run_id: str, token: str, fixture: Path) -> dict[str, object]:
    boundary = f"spectarr-smoke-{uuid.uuid4().hex}"
    separator = chr(59)
    content = fixture.read_bytes()
    prefix = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data{separator} name=\"role\"\r\n\r\nsource\r\n"
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data{separator} name=\"file\""
        f"{separator} filename=\"{fixture.name}\"\r\n"
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body = prefix + content + f"\r\n--{boundary}--\r\n".encode()
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data{separator} boundary={boundary}",
    }
    api_request = request.Request(
        f"{BASE_URL}/runs/{run_id}/artifacts/upload",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(api_request, timeout=60) as response:
            artifact = json.loads(response.read())
    except error.HTTPError as api_error:
        detail = api_error.read().decode(errors="replace")
        raise RuntimeError(f"Artifact upload returned {api_error.code}: {detail}") from api_error
    expected = hashlib.sha256(content).hexdigest()
    if artifact.get("sha256") != expected:
        raise RuntimeError("Uploaded artifact checksum does not match the fixture")
    return artifact


def main() -> int:
    wait_until_ready()
    token = authenticate()
    health = json_call("GET", "/system/health", token=token)
    if not isinstance(health, dict) or health.get("database") != "ok":
        raise RuntimeError(f"Unexpected system health: {health}")

    suffix = uuid.uuid4().hex[:8]
    project = json_call("POST", "/projects", {"name": f"Release rehearsal {suffix}"}, token)
    experiment = json_call(
        "POST",
        "/experiments",
        {"project_id": project["id"], "name": "Clean room smoke"},
        token,
    )
    sample = json_call(
        "POST",
        "/samples",
        {"experiment_id": experiment["id"], "name": "Smoke fixture"},
        token,
    )
    run = json_call(
        "POST",
        "/runs",
        {
            "experiment_id": experiment["id"],
            "sample_id": sample["id"],
            "name": f"smoke-{suffix}",
            "source_class": "open",
        },
        token,
    )
    artifact = upload(str(run["id"]), token, FIXTURE)
    _status, downloaded, _headers = call("GET", f"/artifacts/{artifact['id']}/download", token=token)
    if hashlib.sha256(downloaded).hexdigest() != artifact["sha256"]:
        raise RuntimeError("Downloaded artifact checksum does not match its record")

    deadline = time.monotonic() + 120
    latest = None
    while time.monotonic() < deadline:
        current = json_call("GET", f"/runs/{run['id']}", token=token)
        latest = current.get("latest_extraction")
        if latest:
            break
        time.sleep(2)
    if not latest:
        raise RuntimeError("Metadata extraction did not complete within 120 seconds")

    print(json.dumps({"status": "ok", "run_id": run["id"], "artifact_id": artifact["id"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as smoke_error:
        print(f"Release smoke test failed: {smoke_error}", file=sys.stderr)
        raise SystemExit(1)
