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
MCP_URL = os.getenv("SPECTARR_SMOKE_MCP_URL", "http://127.0.0.1:8281/mcp")
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


def external_json_call(url: str, payload: object) -> object:
    body = json.dumps(payload).encode()
    api_request = request.Request(
        url,
        data=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(api_request, timeout=15) as response:
        return json.loads(response.read())


def wait_for(
    description: str,
    load,
    complete,
    timeout_seconds: float = 300,
):
    deadline = time.monotonic() + timeout_seconds
    latest = None
    while time.monotonic() < deadline:
        latest = load()
        if complete(latest):
            return latest
        time.sleep(2)
    raise RuntimeError(f"Timed out waiting for {description}: {latest}")


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


def authenticate() -> str | None:
    config = json_call("GET", "/auth/config")
    if isinstance(config, dict) and config.get("mode") == "local":
        return None
    status = json_call("GET", "/auth/bootstrap/status")
    endpoint = "/auth/bootstrap" if status == {"required": True} else "/auth/login"
    response = json_call("POST", endpoint, {"username": USERNAME, "password": PASSWORD})
    if not isinstance(response, dict) or not response.get("access_token"):
        raise RuntimeError("Authentication response did not include an access token")
    return str(response["access_token"])


def upload(run_id: str, token: str | None, fixture: Path) -> dict[str, object]:
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
        "Content-Type": f"multipart/form-data{separator} boundary={boundary}",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
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

    mcp = external_json_call(
        MCP_URL,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "release-smoke", "version": "1"}},
        },
    )
    if not isinstance(mcp, dict) or mcp.get("result", {}).get("serverInfo", {}).get("name") != "spectarr-mcp":
        raise RuntimeError(f"Unexpected MCP initialization response: {mcp}")

    suffix = uuid.uuid4().hex[:8]
    webhook = json_call(
        "POST",
        "/webhooks",
        {
            "name": f"Release rehearsal {suffix}",
            "url": "http://127.0.0.1:8001/mcp",
            "event_filters": ["artifact.ready"],
        },
        token,
    )
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

    current = wait_for(
        "metadata extraction",
        lambda: json_call("GET", f"/runs/{run['id']}", token=token),
        lambda value: bool(value.get("latest_extraction")),
        180,
    )
    spectrum = json_call(
        "GET",
        f"/artifacts/{artifact['id']}/spectrum?ms_level=2&index=0",
        token=token,
    )
    if spectrum.get("schema") != "spxtacular.spectrum" or not spectrum.get("arrays", {}).get("mz"):
        raise RuntimeError(f"Spectrum reader returned an invalid payload: {spectrum}")

    recipes = json_call("GET", "/recipes", token=token)
    mzml_recipe = next(
        (recipe for recipe in recipes if recipe.get("output_format") == "mzML" and recipe.get("enabled")),
        None,
    )
    if not mzml_recipe:
        raise RuntimeError("No enabled mzML conversion profile exists")
    conversion = json_call(
        "POST",
        f"/runs/{run['id']}/derivatives",
        {"recipe_id": mzml_recipe["id"]},
        token,
    )
    conversion = wait_for(
        "mzML conversion",
        lambda: json_call("GET", f"/jobs/{conversion['id']}", token=token),
        lambda value: value.get("state") in {"succeeded", "failed", "cancelled"},
        600,
    )
    if conversion.get("state") != "succeeded":
        raise RuntimeError(f"mzML conversion did not succeed: {conversion}")
    output_id = conversion.get("output_artifact_id")
    if not output_id:
        raise RuntimeError("Successful conversion did not record an output artifact")
    output = json_call("GET", f"/artifacts/{output_id}", token=token)
    _status, converted, _headers = call("GET", f"/artifacts/{output_id}/download", token=token)
    if hashlib.sha256(converted).hexdigest() != output.get("sha256"):
        raise RuntimeError("Converted artifact checksum does not match its record")

    delivery = wait_for(
        "webhook delivery",
        lambda: json_call("GET", "/webhook-deliveries", token=token),
        lambda values: any(
            value.get("destination_id") == webhook["id"] and value.get("status") == "delivered"
            for value in values
        ),
        120,
    )

    print(json.dumps({
        "status": "ok",
        "run_id": run["id"],
        "artifact_id": artifact["id"],
        "converted_artifact_id": output_id,
        "mcp": "ok",
        "spectrum_reader": "ok",
        "webhook_delivery_count": len(delivery),
        "spectra_count": current.get("spectraCount"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as smoke_error:
        print(f"Release smoke test failed: {smoke_error}", file=sys.stderr)
        raise SystemExit(1)
