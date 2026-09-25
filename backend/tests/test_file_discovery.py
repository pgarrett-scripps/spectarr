from pathlib import Path

import pytest

from spectarr.config import get_settings
from spectarr.database import SessionLocal
from spectarr.models import Artifact
from spectarr.storage import LocalArtifactStorage
from test_platform import bootstrap
from test_reliability import post, secured_run

pytestmark = pytest.mark.anyio


async def upload(client, run_id, filename="original_100%.mgf"):
    response = await client.post(
        f"/api/v1/runs/{run_id}/artifacts/upload",
        files={"file": (filename, b"BEGIN IONS\n100 50\nEND IONS\n")},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_find_by_file_identity_and_literal_filename(client, hierarchy):
    artifact = await upload(client, hierarchy["run_id"])
    await upload(client, hierarchy["run_id"], "another_100%.mgf")
    for query in (
        "original_100%", artifact["id"], hierarchy["run_id"], artifact["sha256"],
        "sha256:" + artifact["sha256"].upper(), artifact["library_path"], "_100%",
    ):
        response = await client.get("/api/v1/runs", params={"query": query, "page": True})
        assert response.status_code == 200
        page = response.json()
        assert [row["id"] for row in page["items"]] == [hierarchy["run_id"]]
        assert page["total"] == 1
        assert page["next_offset"] is None
    assert (await client.get("/api/v1/runs", params={"query": "originalX100Z"})).json() == []


async def test_access_reports_server_path_and_recorded_checksum(client, hierarchy):
    artifact = await upload(client, hierarchy["run_id"])
    response = await client.get(f"/api/v1/artifacts/{artifact['id']}/access")
    assert response.status_code == 200
    access = response.json()
    assert access["availability"] == "available"
    assert access["project_id"] == hierarchy["project_id"]
    assert access["path_scope"] == "api_server"
    assert Path(access["server_path"]).is_file()
    assert Path(access["library_root"]) / access["library_relative_path"] == Path(access["server_path"])
    assert access["download_url"] == f"/api/v1/artifacts/{artifact['id']}/download"
    assert access["sha256"] == artifact["sha256"]
    assert access["integrity"] == "checksum_recorded_not_reverified"
    assert access["is_directory"] is False


async def test_access_missing_library_keeps_download_and_missing_object_does_not(client, hierarchy):
    artifact = await upload(client, hierarchy["run_id"])
    endpoint = f"/api/v1/artifacts/{artifact['id']}/access"
    access = (await client.get(endpoint)).json()
    Path(access["server_path"]).unlink()
    access = (await client.get(endpoint)).json()
    assert access["availability"] == "unmaterialized"
    assert access["server_path"] is None
    assert access["download_url"]
    storage = LocalArtifactStorage(get_settings().storage_root)
    storage.resolve(artifact["storage_key"]).unlink()
    access = (await client.get(endpoint)).json()
    assert access["availability"] == "missing"
    assert access["download_url"] is None


@pytest.mark.parametrize("state,metadata,availability", [
    ("missing", {"purged_at": "2026-09-24"}, "purged"),
    ("missing", {}, "missing"),
    ("quarantined", {}, "not_ready"),
    ("staging", {}, "not_ready"),
])
async def test_unready_artifacts_never_offer_access_even_if_bytes_exist(client, hierarchy, state, metadata, availability):
    artifact = await upload(client, hierarchy["run_id"])
    with SessionLocal() as session:
        row = session.get(Artifact, artifact["id"])
        row.state = state
        row.metadata_json = metadata
        session.commit()
    access = (await client.get(f"/api/v1/artifacts/{artifact['id']}/access")).json()
    assert access["availability"] == availability
    assert access["server_path"] is None
    assert access["download_url"] is None


async def test_directory_access_does_not_offer_unsupported_download(client, hierarchy, import_root):
    bundle = import_root / "acquisition.d"
    bundle.mkdir()
    (bundle / "data.bin").write_bytes(b"vendor data")
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/import",
        json={"source_path": str(bundle)},
    )
    assert response.status_code == 201, response.text
    access = (await client.get(f"/api/v1/artifacts/{response.json()['id']}/access")).json()
    assert access["is_directory"] is True
    assert Path(access["server_path"]).is_dir()
    assert access["download_url"] is None


async def test_file_discovery_obeys_project_read_permissions(client, password_auth, monkeypatch):
    monkeypatch.setenv("SPECTARR_WORKER_TOKEN", "test-private-worker-token")
    get_settings.cache_clear()
    _, admin = await bootstrap(client)
    visible = await secured_run(client, admin, "Visible")
    hidden = await secured_run(client, admin, "Hidden")
    user = await post(client, "/users", admin, json={
        "username": "file-reader", "password": "reader-test-password", "role": "viewer",
    })
    await post(client, f"/projects/{visible[0]['id']}/memberships", admin, json={"user_id": user["id"], "role": "viewer"})
    token = await post(client, "/tokens", admin, json={
        "user_id": user["id"], "name": "agent file lookup", "scopes": ["library:read"],
    })
    headers = {"Authorization": "Bearer " + token["token"]}
    response = await client.get(f"/api/v1/artifacts/{visible[3]['id']}/access", headers=headers)
    assert response.status_code == 200
    assert (await client.get(f"/api/v1/artifacts/{hidden[3]['id']}/access", headers=headers)).status_code == 403
    for query in (hidden[3]["id"], hidden[3]["sha256"], "Hidden.mgf"):
        page = (await client.get("/api/v1/runs", headers=headers, params={"query": query, "page": True})).json()
        assert page["total"] == 0
        assert page["items"] == []
    assert (await client.get("/api/v1/artifacts/nonexistent/access", headers=headers)).status_code == 404


async def test_rebuild_preserves_last_readable_copy_when_object_is_missing(client, hierarchy):
    artifact = await upload(client, hierarchy["run_id"])
    storage = LocalArtifactStorage(get_settings().storage_root)
    readable = storage.resolve_library(artifact["library_path"])
    content = readable.read_bytes()
    storage.resolve(artifact["storage_key"]).unlink()
    response = await client.post("/api/v1/library/rebuild")
    assert response.status_code == 500
    assert "Existing library preserved" in response.json()["detail"]
    assert readable.read_bytes() == content


async def test_rebuild_preserves_bundle_when_object_member_is_missing(client, hierarchy, import_root):
    bundle = import_root / "rebuild-probe.d"
    bundle.mkdir()
    (bundle / "data.bin").write_bytes(b"last surviving content")
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/import",
        json={"source_path": str(bundle)},
    )
    assert response.status_code == 201
    artifact = response.json()
    storage = LocalArtifactStorage(get_settings().storage_root)
    internal_bundle = storage.resolve(artifact["storage_key"]) / "payload" / bundle.name
    internal_bundle.chmod(0o755)
    (internal_bundle / "data.bin").unlink()
    response = await client.post("/api/v1/library/rebuild")
    assert response.status_code == 500
    assert (storage.resolve_library(artifact["library_path"]) / "data.bin").read_bytes() == b"last surviving content"
