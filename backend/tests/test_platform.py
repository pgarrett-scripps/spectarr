from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect

from spectarr.config import Settings, get_settings
from spectarr.migrations import migration_root, upgrade_database


pytestmark = pytest.mark.anyio


@pytest.fixture
def auth_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTARR_AUTH_ENABLED", "true")
    monkeypatch.setenv("SPECTARR_AUTH_MODE", "password")
    monkeypatch.setenv("SPECTARR_WORKER_TOKEN", "legacy-worker-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def local_auth(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SPECTARR_AUTH_ENABLED", "true")
    monkeypatch.setenv("SPECTARR_AUTH_MODE", "local")
    monkeypatch.setenv("SPECTARR_LOCAL_USER", "admin")
    monkeypatch.setenv("SPECTARR_BIND_ADDRESS", "127.0.0.1")
    monkeypatch.setenv("SPECTARR_WORKER_TOKEN", "local-worker-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def bootstrap(client: AsyncClient) -> tuple[dict, dict[str, str]]:
    status = await client.get("/api/v1/auth/bootstrap/status")
    assert status.json() == {"required": True}
    response = await client.post(
        "/api/v1/auth/bootstrap",
        json={"username": "admin", "password": "very-secure-admin-password"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    return body, {"Authorization": f"Bearer {body['access_token']}"}


async def library_hierarchy(client: AsyncClient, headers: dict[str, str]) -> dict[str, str]:
    project = (await client.post("/api/v1/projects", json={"name": "Secured"}, headers=headers)).json()
    experiment = (
        await client.post(
            "/api/v1/experiments",
            json={"project_id": project["id"], "name": "Agent ingest"},
            headers=headers,
        )
    ).json()
    run = (
        await client.post(
            "/api/v1/runs",
            json={"experiment_id": experiment["id"], "name": "remote-01"},
            headers=headers,
        )
    ).json()
    return {"project_id": project["id"], "experiment_id": experiment["id"], "run_id": run["id"]}


async def test_local_mode_skips_login_and_preserves_credential_precedence(
    client: AsyncClient, local_auth
) -> None:
    configuration = await client.get("/api/v1/auth/config")
    assert configuration.json() == {
        "mode": "local",
        "local_user": "admin",
        "allow_remote_no_auth": False,
    }
    assert (await client.get("/api/v1/auth/bootstrap/status")).json() == {"required": False}
    current = await client.get("/api/v1/auth/me")
    assert current.status_code == 200
    assert current.json()["username"] == "admin"
    assert current.json()["role"] == "admin"
    created = await client.post("/api/v1/projects", json={"name": "No login project"})
    assert created.status_code == 201
    invalid = await client.get(
        "/api/v1/projects",
        headers={"Authorization": "Bearer invalid-token"},
    )
    assert invalid.status_code == 401
    worker = await client.get(
        "/api/v1/projects",
        headers={"X-Spectarr-Worker-Token": "local-worker-secret"},
    )
    assert worker.status_code == 200
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": ""},
    )
    assert login.status_code == 409
    demotion = await client.patch(
        f"/api/v1/users/{current.json()['id']}",
        json={"role": "viewer"},
    )
    assert demotion.status_code == 409


def test_local_mode_requires_explicit_remote_access_acknowledgement() -> None:
    with pytest.raises(ValidationError, match="allow_remote_no_auth"):
        Settings(
            auth_enabled=True,
            auth_mode="local",
            bind_address="0.0.0.0",
            allow_remote_no_auth=False,
        )
    settings = Settings(
        auth_enabled=True,
        auth_mode="local",
        bind_address="0.0.0.0",
        allow_remote_no_auth=True,
    )
    assert settings.effective_auth_mode == "local"


async def test_bootstrap_users_memberships_and_scopes(client: AsyncClient, auth_enabled) -> None:
    _, admin_headers = await bootstrap(client)
    first = (await client.post("/api/v1/projects", json={"name": "Visible"}, headers=admin_headers)).json()
    second = (await client.post("/api/v1/projects", json={"name": "Hidden"}, headers=admin_headers)).json()
    user_response = await client.post(
        "/api/v1/users",
        json={
            "username": "reader",
            "display_name": "Project Reader",
            "password": "very-secure-reader-password",
            "role": "viewer",
        },
        headers=admin_headers,
    )
    assert user_response.status_code == 201
    user = user_response.json()
    assert user["display_name"] == "Project Reader"
    membership = await client.post(
        f"/api/v1/projects/{first['id']}/memberships",
        json={"user_id": user["id"], "role": "viewer"},
        headers=admin_headers,
    )
    assert membership.status_code == 201
    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "reader", "password": "very-secure-reader-password"},
    )
    reader_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    projects = (await client.get("/api/v1/projects", headers=reader_headers)).json()
    assert [project["id"] for project in projects] == [first["id"]]
    assert (await client.get(f"/api/v1/projects/{first['id']}", headers=reader_headers)).status_code == 200
    assert (await client.get(f"/api/v1/projects/{second['id']}", headers=reader_headers)).status_code == 403
    denied = await client.post(
        "/api/v1/experiments",
        json={"project_id": first["id"], "name": "No write"},
        headers=reader_headers,
    )
    assert denied.status_code == 403
    audit = await client.get("/api/v1/audit-log/status", headers=admin_headers)
    assert audit.status_code == 200
    assert audit.json()["entries"] > 0


async def test_login_sessions_are_expiring_hidden_and_revoked_on_logout(
    client: AsyncClient, auth_enabled
) -> None:
    _, admin_headers = await bootstrap(client)
    assert (await client.get("/api/v1/tokens", headers=admin_headers)).json() == []

    created = await client.post(
        "/api/v1/tokens",
        json={"name": "read-only client", "scopes": ["library:read"]},
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    api_token = created.json()
    listed = (await client.get("/api/v1/tokens", headers=admin_headers)).json()
    assert [token["id"] for token in listed] == [api_token["id"]]

    api_headers = {"Authorization": f"Bearer {api_token['token']}"}
    assert (await client.get("/api/v1/projects", headers=api_headers)).status_code == 200
    assert (
        await client.post("/api/v1/projects", json={"name": "Denied"}, headers=api_headers)
    ).status_code == 403

    login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "very-secure-admin-password"},
    )
    login_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert len((await client.get("/api/v1/tokens", headers=login_headers)).json()) == 1
    assert (await client.post("/api/v1/auth/logout", headers=login_headers)).status_code == 204
    assert (await client.get("/api/v1/auth/me", headers=login_headers)).status_code == 401

    assert (
        await client.delete(f"/api/v1/tokens/{api_token['id']}", headers=admin_headers)
    ).status_code == 204
    assert (await client.get("/api/v1/tokens", headers=admin_headers)).json() == []


async def test_password_change_revokes_other_sessions(client: AsyncClient, auth_enabled) -> None:
    _, current_headers = await bootstrap(client)
    other_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "very-secure-admin-password"},
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    changed = await client.post(
        "/api/v1/auth/password",
        json={
            "current_password": "very-secure-admin-password",
            "new_password": "a-different-very-secure-password",
        },
        headers=current_headers,
    )
    assert changed.status_code == 204, changed.text
    assert (await client.get("/api/v1/auth/me", headers=current_headers)).status_code == 200
    assert (await client.get("/api/v1/auth/me", headers=other_headers)).status_code == 401
    old_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "very-secure-admin-password"},
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "a-different-very-secure-password"},
    )
    assert new_login.status_code == 200


async def test_login_throttling_persists_account_lock(client: AsyncClient, auth_enabled) -> None:
    await bootstrap(client)
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login",
            json={"username": "admin", "password": "incorrect-password"},
        )
        assert response.status_code == 401
    locked = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "very-secure-admin-password"},
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


def test_production_settings_reject_development_secrets_and_sqlite() -> None:
    with pytest.raises(ValidationError, match="SPECTARR_SECRET_KEY"):
        Settings(environment="production")
    with pytest.raises(ValidationError, match="PostgreSQL"):
        Settings(
            environment="production",
            secret_key="s" * 32,
            worker_token="w" * 32,
            database_url="sqlite:///spectarr.db",
        )
    settings = Settings(
        environment="production",
        secret_key="s" * 32,
        worker_token="w" * 32,
        database_url="postgresql+psycopg://app:password@db/spectarr",
        cors_origins=["https://spectarr.example.test"],
    )
    assert settings.environment == "production"


async def test_agent_can_be_disabled_and_its_token_rotated(
    client: AsyncClient, auth_enabled
) -> None:
    _, admin_headers = await bootstrap(client)
    registered = await client.post(
        "/api/v1/agents/register",
        json={"name": "managed-agent"},
        headers=admin_headers,
    )
    assert registered.status_code == 201
    agent = registered.json()
    old_headers = {"Authorization": f"Bearer {agent['token']}"}
    heartbeat_path = f"/api/v1/agents/{agent['id']}/heartbeat"
    assert (
        await client.post(heartbeat_path, json={"status": "online"}, headers=old_headers)
    ).status_code == 200

    disabled = await client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"enabled": False},
        headers=admin_headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled.json()["status"] == "disabled"
    assert (
        await client.post(heartbeat_path, json={"status": "online"}, headers=old_headers)
    ).status_code == 401

    rotated = await client.post(
        f"/api/v1/agents/{agent['id']}/rotate-token",
        headers=admin_headers,
    )
    assert rotated.status_code == 200
    new_token = rotated.json()["token"]
    assert new_token.startswith("agt_")
    assert new_token != agent["token"]
    new_headers = {"Authorization": f"Bearer {new_token}"}
    assert (
        await client.post(heartbeat_path, json={"status": "online"}, headers=new_headers)
    ).status_code == 401

    enabled = await client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={"enabled": True},
        headers=admin_headers,
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert (
        await client.post(heartbeat_path, json={"status": "online"}, headers=new_headers)
    ).status_code == 200
    assert (
        await client.post(heartbeat_path, json={"status": "online"}, headers=old_headers)
    ).status_code == 401


async def test_agent_resumable_upload_pipeline_and_extraction(
    client: AsyncClient, auth_enabled
) -> None:
    _, admin_headers = await bootstrap(client)
    hierarchy = await library_hierarchy(client, admin_headers)
    rule = await client.post(
        "/api/v1/automation-rules",
        json={
            "name": "Open conversion",
            "scope": "global",
            "actions": [{"kind": "convert", "parameters": {"format": "mzML"}}],
        },
        headers=admin_headers,
    )
    assert rule.status_code == 201, rule.text
    agent_response = await client.post(
        "/api/v1/agents/register",
        json={
            "name": "instrument-agent",
            "version": "1.0.0",
            "capabilities": ["upload"],
            "metadata_json": {"local_agent_id": "stable-agent-one"},
        },
        headers={**admin_headers, "Idempotency-Key": "register-one"},
    )
    assert agent_response.status_code == 201, agent_response.text
    agent = agent_response.json()
    agent_headers = {"Authorization": f"Bearer {agent['token']}"}
    content = b"resumable source payload"
    digest = hashlib.sha256(content).hexdigest()
    created = await client.post(
        "/api/v1/upload-sessions",
        json={
            "run_id": hierarchy["run_id"],
            "filename": "remote.raw",
            "format": "RAW",
            "total_size": len(content),
            "sha256": digest,
        },
        headers={**agent_headers, "Idempotency-Key": "upload-one"},
    )
    assert created.status_code == 201, created.text
    upload = created.json()
    first = await client.patch(
        f"/api/v1/upload-sessions/{upload['id']}",
        content=content[:8],
        headers={**agent_headers, "Upload-Offset": "0"},
    )
    assert first.status_code == 204
    conflict = await client.patch(
        f"/api/v1/upload-sessions/{upload['id']}",
        content=b"wrong offset",
        headers={**agent_headers, "Upload-Offset": "0"},
    )
    assert conflict.status_code == 409
    second = await client.patch(
        f"/api/v1/upload-sessions/{upload['id']}",
        content=content[8:],
        headers={**agent_headers, "Upload-Offset": "8"},
    )
    assert second.status_code == 204
    completed = await client.post(
        f"/api/v1/upload-sessions/{upload['id']}/complete",
        headers=agent_headers,
    )
    assert completed.status_code == 200, completed.text
    artifact = completed.json()["artifact"]
    second_run = (
        await client.post(
            "/api/v1/runs",
            json={"experiment_id": hierarchy["experiment_id"], "name": "remote-02"},
            headers=admin_headers,
        )
    ).json()
    deduplicated = await client.post(
        "/api/v1/upload-sessions",
        json={
            "run_id": second_run["id"],
            "filename": "same-content.raw",
            "format": "RAW",
            "total_size": len(content),
            "sha256": digest,
        },
        headers={**agent_headers, "Idempotency-Key": "upload-two"},
    )
    assert deduplicated.json()["state"] == "completed"
    assert deduplicated.json()["artifact_id"] != artifact["id"]
    reused = (
        await client.get(
            f"/api/v1/artifacts/{deduplicated.json()['artifact_id']}", headers=admin_headers
        )
    ).json()
    assert reused["storage_key"] == artifact["storage_key"]
    jobs = (await client.get("/api/v1/jobs", headers=admin_headers)).json()
    assert {job["kind"] for job in jobs} == {"extract_metadata", "convert"}
    assert len(jobs) == 4
    assert {job["input_artifact_id"] for job in jobs} == {artifact["id"], reused["id"]}
    conversion = next(
        job for job in jobs if job["kind"] == "convert" and job["input_artifact_id"] == artifact["id"]
    )
    assert conversion["recipe_id"] is not None
    recipe = (await client.get(f"/api/v1/recipes/{conversion['recipe_id']}", headers=admin_headers)).json()
    assert recipe["parameters"] == {
        "filters": [],
        "mz_precision": 64,
        "intensity_precision": 32,
        "compression": "zlib",
        "indexed": True,
    }


    assert set(conversion["parameters"]) == {
        "recipe_fingerprint",
        "recipe_revision",
        "recipe_snapshot",
    }
    assert conversion["parameters"]["recipe_snapshot"]["parameters"] == recipe["parameters"]
    extraction = next(
        job
        for job in jobs
        if job["kind"] == "extract_metadata" and job["input_artifact_id"] == artifact["id"]
    )
    result = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/extraction-results",
        json={
            "schema_version": "1.0",
            "extractor": "fixture-parser",
            "extractor_version": "2.0",
            "result_type": "metadata",
            "payload": {
                "source_format": "mzML",
                "metadata": {"instrument": "Fixture"},
                "qc_summary": {
                    "spectrum_count": 42,
                    "spectra_by_ms_level": {"1": 11, "2": 31},
                    "acquisition_duration_seconds": 3600,
                },
            },
            "warnings": [],
        },
        headers=admin_headers,
    )
    assert result.status_code == 201, result.text
    succeeded = await client.patch(
        f"/api/v1/jobs/{extraction['id']}",
        json={"state": "succeeded", "progress": 1},
        headers=admin_headers,
    )
    assert succeeded.status_code == 200, succeeded.text
    run = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}", headers=admin_headers)).json()
    assert run["latest_extraction"]["payload"]["qc_summary"]["spectrum_count"] == 42
    assert run["spectraCount"] == 42
    assert run["ms2Count"] == 31
    assert run["durationMinutes"] == 60


async def test_agent_inbox_bulk_assignment_and_library_relocation(
    client: AsyncClient, auth_enabled
) -> None:
    _, admin_headers = await bootstrap(client)
    registered = await client.post(
        "/api/v1/agents/register",
        json={"name": "Orbitrap 01", "capabilities": ["upload"]},
        headers=admin_headers,
    )
    assert registered.status_code == 201, registered.text
    agent = registered.json()
    assert agent["destination_mode"] == "inbox"
    assert agent["destination_experiment_id"]
    agent_headers = {"Authorization": f"Bearer {agent['token']}"}

    content = b"instrument inbox payload"
    digest = hashlib.sha256(content).hexdigest()
    upload = await client.post(
        "/api/v1/upload-sessions",
        json={
            "run": {"name": "auto-run-01", "source_class": "vendor"},
            "filename": "auto-run-01.raw",
            "format": "RAW",
            "total_size": len(content),
            "sha256": digest,
        },
        headers={**agent_headers, "Idempotency-Key": "inbox-one"},
    )
    assert upload.status_code == 201, upload.text
    upload_body = upload.json()
    await client.patch(
        f"/api/v1/upload-sessions/{upload_body['id']}",
        content=content,
        headers={**agent_headers, "Upload-Offset": "0"},
    )
    completed = await client.post(
        f"/api/v1/upload-sessions/{upload_body['id']}/complete",
        headers=agent_headers,
    )
    assert completed.status_code == 200, completed.text
    run_id = upload_body["run_id"]
    inbox = (await client.get("/api/v1/inbox", headers=admin_headers)).json()
    assert [run["id"] for run in inbox] == [run_id]
    assert inbox[0]["assignment_status"] == "needs_assignment"
    assert inbox[0]["projectName"].startswith("Instrument Inbox")

    artifact = completed.json()["artifact"]
    old_library_path = Path(os.environ["SPECTARR_STORAGE_ROOT"]) / "library" / artifact["library_path"]
    assert old_library_path.exists()

    destination_project = (
        await client.post("/api/v1/projects", json={"name": "Study Alpha"}, headers=admin_headers)
    ).json()
    destination_experiment = (
        await client.post(
            "/api/v1/experiments",
            json={"project_id": destination_project["id"], "name": "Cohort A"},
            headers=admin_headers,
        )
    ).json()
    moved = await client.post(
        "/api/v1/runs/bulk-assignment",
        json={"run_ids": [run_id], "experiment_id": destination_experiment["id"]},
        headers=admin_headers,
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()[0]["assignment_status"] == "assigned"
    assert moved.json()[0]["projectName"] == "Study Alpha"
    assert (await client.get("/api/v1/inbox", headers=admin_headers)).json() == []
    relocated = (
        await client.get(f"/api/v1/artifacts/{artifact['id']}", headers=admin_headers)
    ).json()
    new_library_path = Path(os.environ["SPECTARR_STORAGE_ROOT"]) / "library" / relocated["library_path"]
    assert new_library_path.exists()
    assert not old_library_path.exists()

    audit = (await client.get("/api/v1/audit-log", headers=admin_headers)).json()
    assert any(entry["action"] == "run.assigned" and entry["resource_id"] == run_id for entry in audit)

    direct = await client.patch(
        f"/api/v1/agents/{agent['id']}",
        json={
            "destination_mode": "direct",
            "destination_experiment_id": destination_experiment["id"],
        },
        headers=admin_headers,
    )
    assert direct.status_code == 200, direct.text
    assert direct.json()["destination_mode"] == "direct"
    direct_content = b"direct destination payload"
    direct_upload = await client.post(
        "/api/v1/upload-sessions",
        json={
            "run": {"name": "auto-run-02", "source_class": "vendor"},
            "filename": "auto-run-02.raw",
            "format": "RAW",
            "total_size": len(direct_content),
            "sha256": hashlib.sha256(direct_content).hexdigest(),
        },
        headers={**agent_headers, "Idempotency-Key": "direct-two"},
    )
    assert direct_upload.status_code == 201, direct_upload.text
    direct_run = (
        await client.get(
            f"/api/v1/runs/{direct_upload.json()['run_id']}", headers=admin_headers
        )
    ).json()
    assert direct_run["assignment_status"] == "assigned"
    assert direct_run["projectName"] == "Study Alpha"


async def test_automation_coalesces_extraction_and_skips_noop_conversion(
    client: AsyncClient, auth_enabled
) -> None:
    _, admin_headers = await bootstrap(client)
    hierarchy = await library_hierarchy(client, admin_headers)
    rule = await client.post(
        "/api/v1/automation-rules",
        json={
            "name": "Open format pipeline",
            "scope": "global",
            "actions": [
                {"kind": "extract_metadata", "parameters": {"deep_qc": True}},
                {"kind": "convert", "parameters": {"format": "mzML"}},
            ],
        },
        headers=admin_headers,
    )
    assert rule.status_code == 201, rule.text
    uploaded = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("already-open.mzML", b"<mzML/>")},
        data={"role": "source"},
        headers=admin_headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    jobs = (await client.get("/api/v1/jobs", headers=admin_headers)).json()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "extract_metadata"
    assert jobs[0]["parameters"]["parameters"]["deep_qc"] is True


async def test_open_derivative_is_extracted_without_conversion_recursion(
    client: AsyncClient, auth_enabled
) -> None:
    _, admin_headers = await bootstrap(client)
    hierarchy = await library_hierarchy(client, admin_headers)
    rule = await client.post(
        "/api/v1/automation-rules",
        json={
            "name": "Convert sources",
            "scope": "global",
            "actions": [{"kind": "convert", "parameters": {"format": "mzML"}}],
        },
        headers=admin_headers,
    )
    assert rule.status_code == 201, rule.text
    uploaded = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("converted.mzML", b"<mzML/>")},
        data={"role": "derived"},
        headers=admin_headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    jobs = (await client.get("/api/v1/jobs", headers=admin_headers)).json()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "extract_metadata"
    assert jobs[0]["input_artifact_id"] == uploaded.json()["id"]


async def test_run_with_derived_qc_downgrades_source_extraction_failure_to_warning(
    client: AsyncClient, auth_enabled
) -> None:
    _, admin_headers = await bootstrap(client)
    hierarchy = await library_hierarchy(client, admin_headers)
    source = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("vendor.raw", b"raw")},
        data={"role": "source"},
        headers=admin_headers,
    )
    assert source.status_code == 201, source.text
    source_job = (await client.get("/api/v1/jobs", headers=admin_headers)).json()[0]
    failed = await client.patch(
        f"/api/v1/jobs/{source_job['id']}",
        json={"state": "failed", "error": "No metadata provider supports RAW"},
        headers=admin_headers,
    )
    assert failed.status_code == 200, failed.text
    derivative = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("converted.mzML", b"<mzML/>")},
        data={"role": "derived"},
        headers=admin_headers,
    )
    assert derivative.status_code == 201, derivative.text
    derivative_id = derivative.json()["id"]
    extraction = await client.post(
        f"/api/v1/artifacts/{derivative_id}/extraction-results",
        json={
            "schema_version": "1.0",
            "extractor": "fixture-parser",
            "extractor_version": "1",
            "result_type": "metadata",
            "payload": {"qc_summary": {"spectrum_count": 10}},
            "warnings": [],
        },
        headers=admin_headers,
    )
    assert extraction.status_code == 201, extraction.text
    jobs = (await client.get("/api/v1/jobs", headers=admin_headers)).json()
    derivative_job = next(job for job in jobs if job["input_artifact_id"] == derivative_id)
    succeeded = await client.patch(
        f"/api/v1/jobs/{derivative_job['id']}",
        json={"state": "succeeded", "progress": 1},
        headers=admin_headers,
    )
    assert succeeded.status_code == 200, succeeded.text
    run = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}", headers=admin_headers)).json()
    assert run["status"] == "warning"
    assert run["spectraCount"] == 10
    reextract = await client.post(
        f"/api/v1/artifacts/{source.json()['id']}/extract",
        json={"extractor": "spectarr-extractor", "schema_version": "1.0", "force": True},
        headers=admin_headers,
    )
    assert reextract.status_code == 202, reextract.text
    cleared = await client.patch(
        f"/api/v1/jobs/{reextract.json()['id']}",
        json={"state": "succeeded", "progress": 1},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    run = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}", headers=admin_headers)).json()
    assert run["status"] == "ready"


async def test_legacy_worker_read_and_job_sequence(client: AsyncClient, auth_enabled) -> None:
    _, admin_headers = await bootstrap(client)
    hierarchy = await library_hierarchy(client, admin_headers)
    uploaded = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("source.mzML", b"mzml")},
        data={"role": "source"},
        headers=admin_headers,
    )
    artifact = uploaded.json()
    worker_headers = {
        "X-Spectarr-Worker-Token": "legacy-worker-secret",
        "X-Spectarr-Worker-Id": "extractor-one",
    }
    assert (await client.get(f"/api/v1/artifacts/{artifact['id']}", headers=worker_headers)).status_code == 200
    assert (
        await client.get(f"/api/v1/artifacts/{artifact['id']}/location", headers=worker_headers)
    ).status_code == 200
    jobs = (await client.get("/api/v1/jobs?kind=extract_metadata&state=queued", headers=worker_headers)).json()
    claimed = await client.post(f"/api/v1/jobs/{jobs[0]['id']}/claim", headers=worker_headers)
    assert claimed.status_code == 200
    heartbeat = await client.post(f"/api/v1/jobs/{jobs[0]['id']}/heartbeat", headers=worker_headers)
    assert heartbeat.status_code == 200


async def test_agent_bundle_upload_and_manifest_deduplication(client: AsyncClient, auth_enabled) -> None:
    _, admin_headers = await bootstrap(client)
    hierarchy = await library_hierarchy(client, admin_headers)
    agent = (
        await client.post(
            "/api/v1/agents/register",
            json={"name": "bundle-agent", "capabilities": ["bundle-upload"]},
            headers={**admin_headers, "Idempotency-Key": "bundle-agent"},
        )
    ).json()
    agent_headers = {"Authorization": f"Bearer {agent['token']}"}
    contents = {"analysis.baf": b"binary", "AcqData/method.xml": b"<method/>"}
    manifest = {
        "root_name": "fixture.d",
        "files": [
            {"path": path, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for path, content in contents.items()
        ],
    }
    created = await client.post(
        "/api/v1/upload-sessions",
        json={
            "run_id": hierarchy["run_id"],
            "filename": "fixture.d",
            "format": "vendor_directory",
            "bundle_manifest": manifest,
        },
        headers={**agent_headers, "Idempotency-Key": "bundle-upload"},
    )
    assert created.status_code == 201, created.text
    upload = created.json()
    assert {part["path"]: part["offset"] for part in upload["files"]} == {
        "AcqData/method.xml": 0,
        "analysis.baf": 0,
    }
    for path, content in contents.items():
        response = await client.patch(
            f"/api/v1/upload-sessions/{upload['id']}/files/{path}",
            content=content,
            headers={**agent_headers, "Upload-Offset": "0"},
        )
        assert response.status_code == 204, response.text
    completed = await client.post(
        f"/api/v1/upload-sessions/{upload['id']}/complete", headers=agent_headers
    )
    assert completed.status_code == 200, completed.text
    artifact = completed.json()["artifact"]
    assert artifact["bundle_manifest"]["root_name"] == "fixture.d"
    second_run = (
        await client.post(
            "/api/v1/runs",
            json={"experiment_id": hierarchy["experiment_id"], "name": "bundle-copy"},
            headers=admin_headers,
        )
    ).json()
    deduplicated = await client.post(
        "/api/v1/upload-sessions",
        json={
            "run_id": second_run["id"],
            "filename": "fixture.d",
            "format": "vendor_directory",
            "bundle_manifest": manifest,
        },
        headers={**agent_headers, "Idempotency-Key": "bundle-copy"},
    )
    assert deduplicated.status_code == 201
    assert deduplicated.json()["state"] == "completed"
    assert deduplicated.json()["artifact_id"] != artifact["id"]


async def test_webhook_outbox_delivery_is_worker_driven(client: AsyncClient, auth_enabled) -> None:
    _, admin_headers = await bootstrap(client)
    webhook = await client.post(
        "/api/v1/webhooks",
        json={"name": "searcharr", "url": "https://searcharr.test/hooks", "event_filters": ["artifact.ready"]},
        headers=admin_headers,
    )
    assert webhook.status_code == 201
    assert webhook.json()["signing_secret"].startswith("whsec_")
    hierarchy = await library_hierarchy(client, admin_headers)
    await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("source.mzML", b"mzml")},
        data={"role": "source"},
        headers=admin_headers,
    )
    deliveries = (await client.get("/api/v1/webhook-deliveries", headers=admin_headers)).json()
    assert len(deliveries) == 1
    claimed = await client.post(
        f"/api/v1/webhook-deliveries/{deliveries[0]['id']}/claim", headers=admin_headers
    )
    assert claimed.status_code == 200
    assert claimed.json()["signing_secret"].startswith("whsec_")
    delivered = await client.patch(
        f"/api/v1/webhook-deliveries/{deliveries[0]['id']}",
        json={"status": "delivered", "response_status": 204},
        headers=admin_headers,
    )
    assert delivered.status_code == 200


def test_alembic_migrates_an_empty_database(tmp_path: Path) -> None:
    database = tmp_path / "migrated.db"
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, "head")
    tables = set(inspect(create_engine(f"sqlite:///{database}")).get_table_names())
    assert {"alembic_version", "users", "upload_sessions", "extraction_results", "event_outbox"} <= tables


def test_migration_root_can_be_configured_for_an_installed_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "alembic.ini").touch()
    (tmp_path / "alembic").mkdir()
    monkeypatch.setenv("SPECTARR_MIGRATION_ROOT", str(tmp_path))
    monkeypatch.chdir(tmp_path.parent)
    assert migration_root() == tmp_path


def test_legacy_schema_is_adopted_without_data_loss(tmp_path: Path) -> None:
    database = tmp_path / "legacy.db"
    database_url = f"sqlite:///{database}"
    legacy = create_engine(database_url)
    with legacy.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY, name VARCHAR(255) NOT NULL UNIQUE, "
            "description TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE experiments (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36) NOT NULL, "
            "name VARCHAR(255) NOT NULL, description TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE runs (id VARCHAR(36) PRIMARY KEY, experiment_id VARCHAR(36) NOT NULL, "
            "sample_id VARCHAR(36), instrument_id VARCHAR(36), name VARCHAR(255) NOT NULL, "
            "source_class VARCHAR(32) NOT NULL, acquired_at DATETIME, metadata_json JSON NOT NULL, "
            "created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE artifacts (id VARCHAR(36) PRIMARY KEY, run_id VARCHAR(36) NOT NULL, "
            "parent_artifact_id VARCHAR(36), recipe_id VARCHAR(36), role VARCHAR(32) NOT NULL, "
            "state VARCHAR(32) NOT NULL, format VARCHAR(32) NOT NULL, original_filename VARCHAR(1024) NOT NULL, "
            "storage_key VARCHAR(1024) NOT NULL, byte_size INTEGER NOT NULL, sha256 VARCHAR(64) NOT NULL, "
            "bundle_manifest JSON, recipe_fingerprint VARCHAR(64), metadata_json JSON NOT NULL, "
            "immutable BOOLEAN NOT NULL, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)"
        )
        now = "2026-01-01 00:00:00"
        connection.exec_driver_sql(
            "INSERT INTO projects VALUES ('p1','Legacy',NULL,?,?)", (now, now)
        )
        connection.exec_driver_sql(
            "INSERT INTO experiments VALUES ('e1','p1','Legacy experiment',NULL,?,?)", (now, now)
        )
        connection.exec_driver_sql(
            "INSERT INTO runs VALUES ('r1','e1',NULL,NULL,'Legacy run','open',NULL,'{}',?,?)", (now, now)
        )
        connection.exec_driver_sql(
            "INSERT INTO artifacts VALUES "
            "('a1','r1',NULL,NULL,'source','ready','mzML','legacy.mzML','objects/legacy',1,"
            "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',NULL,NULL,'{}',1,?,?)",
            (now, now),
        )
    legacy.dispose()
    upgrade_database(database_url)
    migrated = create_engine(database_url)
    with migrated.connect() as connection:
        assert connection.exec_driver_sql("SELECT name FROM projects WHERE id='p1'").scalar_one() == "Legacy"
        assert connection.exec_driver_sql("SELECT name FROM runs WHERE id='r1'").scalar_one() == "Legacy run"
        assert connection.exec_driver_sql("SELECT original_filename FROM artifacts WHERE id='a1'").scalar_one() == "legacy.mzML"
    assert "alembic_version" in inspect(migrated).get_table_names()
