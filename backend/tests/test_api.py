from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient

from spectarr.database import SessionLocal
from spectarr.models import Job


pytestmark = pytest.mark.anyio


async def test_health_and_openapi(client: AsyncClient) -> None:
    root_health = await client.get("/health")
    assert root_health.json()["status"] == "ok"
    assert root_health.headers["X-Content-Type-Options"] == "nosniff"
    assert root_health.headers["X-Frame-Options"] == "DENY"
    auth_configuration = await client.get("/api/v1/auth/config")
    assert auth_configuration.headers["Cache-Control"] == "no-store"
    health = await client.get("/api/v1/system/health")
    assert health.status_code == 200
    assert health.json()["database"] == "ok"
    schema = (await client.get("/openapi.json")).json()
    assert "/api/v1/runs/{run_id}/derivatives" in schema["paths"]
    dashboard_operations = {
        "/api/v1/auth/bootstrap": {"post"},
        "/api/v1/auth/login": {"post"},
        "/api/v1/auth/logout": {"post"},
        "/api/v1/auth/password": {"post"},
        "/api/v1/projects": {"post"},
        "/api/v1/projects/{project_id}/memberships": {"post"},
        "/api/v1/projects/{project_id}/memberships/{membership_id}": {"delete"},
        "/api/v1/runs/{run_id}/artifacts/upload": {"post"},
        "/api/v1/runs/{run_id}/artifacts/import": {"post"},
        "/api/v1/artifacts/{artifact_id}/download": {"get"},
        "/api/v1/runs/{run_id}/derivatives": {"post"},
        "/api/v1/recipes/{recipe_id}": {"patch"},
        "/api/v1/processing-batches/preview": {"post"},
        "/api/v1/processing-batches": {"get", "post"},
        "/api/v1/processing-batches/{batch_id}": {"get"},
        "/api/v1/processing-batches/{batch_id}/retry": {"post"},
        "/api/v1/processing-batches/{batch_id}/cancel": {"post"},
        "/api/v1/artifacts/{artifact_id}/extract": {"post"},
        "/api/v1/jobs/{job_id}/retry": {"post"},
        "/api/v1/agents/register": {"post"},
        "/api/v1/agents/{agent_id}": {"patch"},
        "/api/v1/agents/{agent_id}/rotate-token": {"post"},
        "/api/v1/inbox": {"get"},
        "/api/v1/runs/bulk-assignment": {"post"},
        "/api/v1/runs/{run_id}/assignment": {"patch"},
        "/api/v1/automation-rules": {"post"},
        "/api/v1/automation-rules/{rule_id}": {"patch"},
        "/api/v1/users": {"post"},
        "/api/v1/users/{user_id}": {"patch"},
        "/api/v1/tokens": {"post"},
        "/api/v1/tokens/{token_id}": {"delete"},
        "/api/v1/webhooks": {"post"},
        "/api/v1/webhooks/{webhook_id}": {"patch", "delete"},
        "/api/v1/experiments/{experiment_id}/deletion-preview": {"get"},
        "/api/v1/experiments/{experiment_id}": {"delete"},
        "/api/v1/storage/reclaim/preview": {"post"},
        "/api/v1/storage/reclaim": {"post"},
        "/api/v1/projects/{project_id}/sdrf": {"get", "put"},
        "/api/v1/projects/{project_id}/sdrf/import": {"post"},
        "/api/v1/projects/{project_id}/sdrf/generate": {"post"},
        "/api/v1/projects/{project_id}/sdrf/validate": {"post"},
        "/api/v1/projects/{project_id}/sdrf/export": {"get"},
        "/api/v1/projects/{project_id}/submission/preview": {"get"},
        "/api/v1/projects/{project_id}/submission/export": {"get"},
    }
    for path, methods in dashboard_operations.items():
        assert methods.issubset(schema["paths"][path])
    overview = await client.get("/api/v1/overview")
    assert overview.status_code == 200
    assert overview.json()["health"]["api"] == "online"


async def test_library_hierarchy(client: AsyncClient, hierarchy: dict[str, str]) -> None:
    run = await client.get(f"/api/v1/runs/{hierarchy['run_id']}")
    assert run.status_code == 200
    assert run.json()["name"] == "hela-01"
    projects = (await client.get("/api/v1/projects")).json()
    assert [project["name"] for project in projects] == ["Proteomics"]


async def test_rejects_sample_from_another_experiment(client: AsyncClient, hierarchy: dict[str, str]) -> None:
    experiment = (await client.post(
        "/api/v1/experiments",
        json={"project_id": hierarchy["project_id"], "name": "Other"},
    )).json()
    response = await client.post(
        "/api/v1/runs",
        json={
            "experiment_id": experiment["id"],
            "sample_id": hierarchy["sample_id"],
            "name": "invalid-run",
        },
    )
    assert response.status_code == 422


async def test_upload_is_content_addressed_and_downloadable(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    content = b"minimal mzML test fixture"
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("fixture.mzML", content, "application/octet-stream")},
        data={"role": "source"},
    )
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["sha256"] == hashlib.sha256(content).hexdigest()
    assert artifact["format"] == "mzML"
    assert artifact["immutable"] is True
    downloaded = await client.get(f"/api/v1/artifacts/{artifact['id']}/download")
    assert downloaded.content == content
    global_artifacts = (await client.get("/api/v1/artifacts", params={"format": "mzml"})).json()
    assert [item["id"] for item in global_artifacts] == [artifact["id"]]

    assert "/mzml/" in artifact["library_path"]
    assert artifact["library_path"].endswith(f"hela-01__HeLa__{hierarchy['run_id'].replace('-', '')[:8]}.mzML")
    location = (await client.get(f"/api/v1/artifacts/{artifact['id']}/location")).json()
    assert "/mzml/" in location["path"]
    manifest = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}/manifest")).json()
    assert manifest["schema"] == "spectarr.library/v2"
    assert manifest["run"]["name"] == "hela-01"
    assert manifest["artifacts"][0]["path"].startswith("mzml/")
    project_library = (await client.get(f"/api/v1/projects/{hierarchy['project_id']}/library")).json()
    assert project_library["formats"]["mzml"]["artifact_count"] == 1
    project_manifest = (await client.get(
        f"/api/v1/projects/{hierarchy['project_id']}/manifest"
    )).json()
    assert project_manifest["runs"][0]["experiment_id"] == hierarchy["experiment_id"]


async def test_duplicate_filenames_get_stable_collision_suffix_and_rebuild(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    first = (await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("sample.mgf", b"first")},
        data={"role": "source"},
    )).json()
    second = (await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("sample.mgf", b"second")},
        data={"role": "source"},
    )).json()
    base_name = f"hela-01__HeLa__{hierarchy['run_id'].replace('-', '')[:8]}"
    assert first["library_path"].endswith(f"/mgf/{base_name}.mgf")
    assert second["library_path"].endswith(
        f"/mgf/{base_name}__{second['id'].replace('-', '')[:8]}.mgf"
    )

    rebuilt = await client.post("/api/v1/library/rebuild")
    assert rebuilt.status_code == 200, rebuilt.text
    assert rebuilt.json()["artifacts"] == 2
    status_response = (await client.get("/api/v1/library")).json()
    assert status_response["healthy"] is True
    assert status_response["materialized_artifacts"] == 2
    manifest = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}/manifest")).json()
    assert {item["original_filename"] for item in manifest["artifacts"]} == {"sample.mgf"}
    assert len(manifest["artifacts"]) == 2


async def test_upload_rejects_checksum_mismatch(client: AsyncClient, hierarchy: dict[str, str]) -> None:
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("fixture.mzML", b"converted")},
        data={"role": "source", "expected_sha256": "0" * 64},
    )
    assert response.status_code == 422
    assert "checksum" in response.json()["detail"]


async def test_upload_recognizes_gzipped_mzml(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("fixture.mzML.gz", b"compressed")},
        data={"role": "source"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["format"] == "mzML"


async def test_run_projection_preserves_mzxml_format(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("fixture.mzXML", b"<mzXML/>")},
        data={"role": "derived"},
    )
    assert response.status_code == 201, response.text
    run = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}")).json()
    artifact = next(item for item in run["artifacts"] if item["id"] == response.json()["id"])
    assert artifact["format"] == "mzXML"


async def test_reclaim_purges_only_regenerable_derivatives(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    source_response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("sample.raw", b"source-data")},
        data={"role": "source"},
    )
    assert source_response.status_code == 201, source_response.text
    source = source_response.json()
    profiles = (await client.get("/api/v1/recipes")).json()
    mgf_profile = next(profile for profile in profiles if profile["output_format"] == "MGF")
    derived_response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("sample.mgf", b"BEGIN IONS\nEND IONS\n")},
        data={
            "role": "derived",
            "parent_artifact_id": source["id"],
            "recipe_id": mgf_profile["id"],
        },
    )
    assert derived_response.status_code == 201, derived_response.text
    derived = derived_response.json()
    for job in (await client.get("/api/v1/jobs")).json():
        if job["input_artifact_id"] == derived["id"] and job["state"] in {"queued", "running"}:
            cancelled = await client.patch(
                f"/api/v1/jobs/{job['id']}",
                json={"state": "cancelled"},
            )
            assert cancelled.status_code == 200, cancelled.text
    library_root = Path(os.environ["SPECTARR_STORAGE_ROOT"]) / "library"
    source_path = library_root / source["library_path"]
    derived_path = library_root / derived["library_path"]
    source_object = Path(os.environ["SPECTARR_STORAGE_ROOT"]) / source["storage_key"]
    derived_object = Path(os.environ["SPECTARR_STORAGE_ROOT"]) / derived["storage_key"]
    assert source_path.exists()
    assert derived_path.exists()
    assert source_object.exists()
    assert derived_object.exists()

    request = {
        "scope_type": "project",
        "scope_ids": [hierarchy["project_id"]],
        "formats": ["MGF"],
    }
    preview = await client.post("/api/v1/storage/reclaim/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "artifact_count": 1,
        "reclaimable_bytes": len(b"BEGIN IONS\nEND IONS\n"),
        "format_counts": {"MGF": 1},
        "blocked_count": 0,
    }
    invalid = await client.post(
        "/api/v1/storage/reclaim",
        json={**request, "confirmation": "wrong"},
    )
    assert invalid.status_code == 422
    reclaimed = await client.post(
        "/api/v1/storage/reclaim",
        json={**request, "confirmation": "PURGE DERIVED FILES"},
    )
    assert reclaimed.status_code == 200, reclaimed.text
    assert reclaimed.json()["artifact_count"] == 1
    assert source_path.exists()
    assert not derived_path.exists()
    assert source_object.exists()
    assert not derived_object.exists()
    assert (await client.get(f"/api/v1/artifacts/{source['id']}/download")).status_code == 200
    unavailable = await client.get(f"/api/v1/artifacts/{derived['id']}/download")
    assert unavailable.status_code == 410
    stored = (await client.get(f"/api/v1/artifacts/{derived['id']}")).json()
    assert stored["state"] == "missing"
    assert stored["library_path"] is None
    assert stored["sha256"] == derived["sha256"]


async def test_experiment_deletion_requires_name_and_removes_files(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    uploaded = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("sample.mzML", b"<mzML/>")},
        data={"role": "source"},
    )
    assert uploaded.status_code == 201, uploaded.text
    artifact = uploaded.json()
    library_path = Path(os.environ["SPECTARR_STORAGE_ROOT"]) / "library" / artifact["library_path"]
    assert library_path.exists()
    preview = await client.get(
        f"/api/v1/experiments/{hierarchy['experiment_id']}/deletion-preview"
    )
    assert preview.status_code == 200
    assert preview.json()["run_count"] == 1
    assert preview.json()["source_count"] == 1
    for job in (await client.get("/api/v1/jobs")).json():
        if job["state"] in {"queued", "running"}:
            cancelled = await client.patch(
                f"/api/v1/jobs/{job['id']}",
                json={"state": "cancelled"},
            )
            assert cancelled.status_code == 200, cancelled.text
    mismatch = await client.delete(
        f"/api/v1/experiments/{hierarchy['experiment_id']}",
        params={"confirmation": "not-the-name"},
    )
    assert mismatch.status_code == 422
    deleted = await client.delete(
        f"/api/v1/experiments/{hierarchy['experiment_id']}",
        params={"confirmation": "Benchmark"},
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["logical_bytes"] == len(b"<mzML/>")
    assert (await client.get(f"/api/v1/experiments/{hierarchy['experiment_id']}")).status_code == 404
    assert (await client.get(f"/api/v1/runs/{hierarchy['run_id']}")).status_code == 404
    assert not library_path.exists()


async def test_directory_import_creates_canonical_manifest(
    client: AsyncClient,
    hierarchy: dict[str, str],
    import_root: Path,
) -> None:
    bundle = import_root / "sample.d"
    bundle.mkdir(exist_ok=True)
    (bundle / "analysis.baf").write_bytes(b"abc")
    nested = bundle / "AcqData"
    nested.mkdir(exist_ok=True)
    (nested / "method.xml").write_text("<method />")
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/import",
        json={"source_path": str(bundle), "role": "source"},
    )
    assert response.status_code == 201, response.text
    artifact = response.json()
    assert artifact["format"] == "vendor_directory"
    assert artifact["bundle_manifest"]["version"] == 1
    assert artifact["bundle_manifest"]["root_name"] == "sample.d"
    assert [item["path"] for item in artifact["bundle_manifest"]["files"]] == [
        "AcqData/method.xml",
        "analysis.baf",
    ]
    location = (await client.get(f"/api/v1/artifacts/{artifact['id']}/location")).json()
    assert location["is_bundle"] is True
    expected_name = f"hela-01__HeLa__{hierarchy['run_id'].replace('-', '')[:8]}.d"
    assert location["path"].endswith(f"/raw/{expected_name}")
    assert location["relative_path"].endswith(f"/raw/{expected_name}")
    assert artifact["library_path"].endswith(f"/raw/{expected_name}")


async def test_path_import_must_be_allowlisted(
    client: AsyncClient,
    hierarchy: dict[str, str],
    tmp_path: Path,
) -> None:
    source = tmp_path / "outside.mgf"
    source.write_text("BEGIN IONS\nEND IONS\n")
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/import",
        json={"source_path": str(source)},
    )
    assert response.status_code == 403


async def test_conversion_recipe_and_job_lifecycle(
    client: AsyncClient,
    hierarchy: dict[str, str],
) -> None:
    source = (await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("source.raw", b"vendor bytes")},
        data={"role": "source"},
    )).json()
    recipe_response = await client.post(
        "/api/v1/recipes",
        json={
            "name": "default-mzml",
            "output_format": "mzML",
            "parameters": {
                "filters": [{"kind": "ms_level", "levels": [1, 2]}],
                "compression": "zlib",
            },
        },
    )
    assert recipe_response.status_code == 201, recipe_response.text
    recipe = recipe_response.json()
    queued = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/derivatives",
        json={"input_artifact_id": source["id"], "recipe_id": recipe["id"]},
    )
    assert queued.status_code == 202, queued.text
    job = queued.json()
    assert job["state"] == "queued"
    assert len(job["parameters"]["recipe_fingerprint"]) == 64
    claimable = (await client.get("/api/v1/jobs?kind=convert&claimable=true")).json()
    assert job["id"] in {value["id"] for value in claimable}

    running = await client.post(f"/api/v1/jobs/{job['id']}/claim")
    assert running.status_code == 200
    assert running.json()["started_at"] is not None
    assert running.json()["attempts"] == 1
    duplicate_claim = await client.post(f"/api/v1/jobs/{job['id']}/claim")
    assert duplicate_claim.status_code == 409
    active = (await client.get("/api/v1/jobs?kind=convert&claimable=true")).json()
    assert job["id"] not in {value["id"] for value in active}
    with SessionLocal() as session:
        stored_job = session.get(Job, job["id"])
        assert stored_job is not None
        stored_job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
    expired = (await client.get("/api/v1/jobs?kind=convert&claimable=true")).json()
    assert job["id"] in {value["id"] for value in expired}
    reclaimed = await client.post(f"/api/v1/jobs/{job['id']}/claim")
    assert reclaimed.status_code == 200
    assert reclaimed.json()["attempts"] == 2
    progress = await client.patch(f"/api/v1/jobs/{job['id']}", json={"progress": 0.25})
    assert progress.json()["progress"] == 0.25

    derived = (await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("source.mzML", b"converted bytes")},
        data={
            "role": "derived",
            "format": "mzML",
            "parent_artifact_id": source["id"],
            "recipe_id": recipe["id"],
        },
    )).json()
    completed = await client.patch(
        f"/api/v1/jobs/{job['id']}",
        json={"state": "succeeded", "output_artifact_id": derived["id"]},
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["progress"] == 1.0


async def test_named_profiles_and_project_processing_batch(
    client: AsyncClient,
    hierarchy: dict[str, str],
) -> None:
    profiles = (await client.get("/api/v1/recipes")).json()
    assert {profile["name"] for profile in profiles} >= {"Standard mzML", "Standard MGF"}
    mgf = next(profile for profile in profiles if profile["name"] == "Standard MGF")
    mzml = next(profile for profile in profiles if profile["name"] == "Standard mzML")
    assert mgf["system"] is True
    assert mgf["revision"] == 1
    default_rule = (await client.get("/api/v1/automation-rules")).json()
    assert default_rule[0]["actions"] == [{"kind": "convert", "recipe_id": mzml["id"]}]
    named = await client.post(
        "/api/v1/recipes",
        json={
            "name": "Sage search",
            "description": "Use the packaged MSCLI Sage config",
            "output_format": "mzML",
            "parameters": {"preset": "sage"},
        },
    )
    assert named.status_code == 201, named.text
    assert named.json()["parameters"]["preset"] == "sage"
    invalid_named = await client.post(
        "/api/v1/recipes",
        json={"name": "Invalid Sage MGF", "output_format": "MGF", "parameters": {"preset": "sage"}},
    )
    assert invalid_named.status_code == 422

    second_run = (await client.post(
        "/api/v1/runs",
        json={"experiment_id": hierarchy["experiment_id"], "name": "hela-02", "source_class": "vendor"},
    )).json()
    first_source = (await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("first.raw", b"first vendor source")},
        data={"role": "source"},
    )).json()
    await client.post(
        f"/api/v1/runs/{second_run['id']}/artifacts/upload",
        files={"file": ("second.raw", b"second vendor source")},
        data={"role": "source"},
    )
    request = {
        "scope_type": "project",
        "scope_ids": [hierarchy["project_id"]],
        "recipe_ids": [mgf["id"]],
        "mode": "missing",
    }
    preview = await client.post("/api/v1/processing-batches/preview", json=request)
    assert preview.status_code == 200, preview.text
    assert preview.json() == {
        "scope_type": "project",
        "run_count": 2,
        "target_count": 2,
        "queue_count": 2,
        "current_count": 0,
        "stale_count": 0,
        "incompatible_count": 0,
        "queued_count": 0,
    }
    queued = await client.post("/api/v1/processing-batches", json=request)
    assert queued.status_code == 202, queued.text
    batch = queued.json()
    assert batch["state"] == "queued"
    assert batch["queued_count"] == 2
    assert {item["run_name"] for item in batch["items"]} == {"hela-01", "hela-02"}
    assert all(item["recipe_name"] == "Standard MGF" for item in batch["items"])

    first_item = next(item for item in batch["items"] if item["run_id"] == hierarchy["run_id"])
    job = (await client.get(f"/api/v1/jobs/{first_item['job_id']}")).json()
    assert job["parameters"]["recipe_revision"] == 1
    assert job["parameters"]["recipe_snapshot"]["name"] == "Standard MGF"
    derived = (await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("first.mgf", b"BEGIN IONS\nEND IONS\n")},
        data={
            "role": "derived",
            "format": "MGF",
            "parent_artifact_id": first_source["id"],
            "recipe_id": mgf["id"],
            "recipe_fingerprint": job["parameters"]["recipe_fingerprint"],
        },
    )).json()
    await client.patch(
        f"/api/v1/jobs/{job['id']}",
        json={"state": "succeeded", "output_artifact_id": derived["id"]},
    )
    current = (await client.post("/api/v1/processing-batches/preview", json=request)).json()
    assert current["current_count"] == 1
    assert current["queued_count"] == 1
    remaining_item = next(item for item in batch["items"] if item["job_id"] != job["id"])
    running = await client.post(f"/api/v1/jobs/{remaining_item['job_id']}/claim")
    assert running.status_code == 200, running.text
    assert running.json()["state"] == "running"
    cancelled = await client.post(f"/api/v1/processing-batches/{batch['id']}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["cancelled_count"] == 1
    cancelled_job = (await client.get(f"/api/v1/jobs/{remaining_item['job_id']}")).json()
    assert cancelled_job["state"] == "cancelled"
    assert cancelled_job["lease_expires_at"] is None
    retried = await client.post(f"/api/v1/processing-batches/{batch['id']}/retry")
    assert retried.status_code == 200, retried.text
    assert retried.json()["state"] == "queued"

    updated = await client.patch(
        f"/api/v1/recipes/{mgf['id']}",
        json={
            "parameters": {
                **mgf["parameters"],
                "intensity_precision": 64,
            }
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision"] == 2
    stale = (await client.post("/api/v1/processing-batches/preview", json=request)).json()
    assert stale["stale_count"] == 1
    request["mode"] = "missing_or_stale"
    refresh = (await client.post("/api/v1/processing-batches/preview", json=request)).json()
    assert refresh["queue_count"] == 2
    assert refresh["stale_count"] == 1


async def test_annotations(client: AsyncClient, hierarchy: dict[str, str]) -> None:
    response = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/annotations",
        json={"author": "operator", "body": "Good QC", "tags": ["qc-pass"]},
    )
    assert response.status_code == 201
    assert response.json()["tags"] == ["qc-pass"]
