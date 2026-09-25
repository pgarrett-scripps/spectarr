from __future__ import annotations

import json
import asyncio
import threading
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from spectarr.config import get_settings
from spectarr.database import SessionLocal
from spectarr.library import LibraryMaterializer
from spectarr.library_publication import LibraryPublication, publication_journal
from spectarr.locking import maintenance_lock
from spectarr.models import AuditLog, Job, JobState, Project, Run
from spectarr.storage import LocalArtifactStorage

pytestmark = pytest.mark.anyio


async def test_manifest_upgrade_refreshes_summaries_without_touching_acquisition_files(client, hierarchy, monkeypatch):
    source = await upload(client, hierarchy["run_id"], "upgrade.raw")
    await extract(client, source, 100)
    storage = LocalArtifactStorage(get_settings().storage_root)
    materializer = LibraryMaterializer(storage)
    with SessionLocal() as session:
        run = session.get(Run, hierarchy["run_id"])
        key = materializer.run_manifest_key(run)
        payload = json.loads(storage.resolve_library(key).read_text())
        payload["run"]["metadata"]["spectra_count"] = 40
        payload["run"].pop("summary_basis")
        payload["run"]["acquired_at"] = run.created_at.isoformat()
        payload["custom_note"] = "Preserve unrelated fields"
        storage.write_library_json(key, payload)
        path = storage.resolve_library(source["library_path"])
        original = (path.stat().st_ino, path.read_bytes())
        def forbid_materialization(*args, **kwargs):
            raise AssertionError("Summary refresh must not move acquisitions")
        monkeypatch.setattr(LocalArtifactStorage, "materialize", forbid_materialization)
        assert materializer.refresh_run_summaries(session) == 1
        assert materializer.refresh_run_summaries(session) == 0
        assert (path.stat().st_ino, path.read_bytes()) == original
        updated = json.loads(storage.resolve_library(key).read_text())
        assert updated["artifacts"] == payload["artifacts"]
        assert updated["custom_note"] == payload["custom_note"]
    view = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}/manifest")).json()["run"]
    assert view["metadata"]["spectra_count"] == 100
    assert view["summary_basis"]["artifact_id"] == source["id"]
    assert view["acquired_at"] is None


async def test_running_worker_keeps_lease_when_rebuild_or_project_update_is_rejected(client, hierarchy):
    source = await upload(client, hierarchy["run_id"], "active.mgf")
    with SessionLocal() as session:
        job = session.scalar(select(Job).where(Job.input_artifact_id == source["id"]))
        job.state = JobState.RUNNING
        job.worker_id = "anonymous-worker"
        session.commit()
        job_id = job.id
    for method, url, body in (
        ("POST", "/api/v1/library/rebuild", None),
        ("PATCH", f"/api/v1/projects/{hierarchy['project_id']}", {"name": "Must wait"}),
    ):
        response = await client.request(method, url, json=body)
        assert response.status_code == 409, response.text
        assert not publication_journal(get_settings().storage_root / "library").exists()
        assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 200
        assert (await client.post(f"/api/v1/jobs/{job_id}/heartbeat")).status_code == 200
    assert (await client.patch(f"/api/v1/jobs/{job_id}", json={"state": "succeeded"})).status_code == 200
    assert (await client.patch(f"/api/v1/projects/{hierarchy['project_id']}", json={"name": "Now safe"})).status_code == 200


async def test_worker_cannot_claim_between_idle_check_and_rebuild(client, hierarchy, monkeypatch):
    from spectarr import maintenance
    source = await upload(client, hierarchy["run_id"], "queued.mgf")
    with SessionLocal() as session:
        job_id = session.scalar(select(Job.id).where(Job.input_artifact_id == source["id"]))
    entered = threading.Event()
    attempted = threading.Event()
    release = threading.Event()
    original_rebuild = LibraryMaterializer.rebuild
    original_lock = maintenance.file_lock
    def paused_rebuild(self, session):
        entered.set()
        if not release.wait(5):
            raise RuntimeError("Rebuild test was not released")
        return original_rebuild(self, session)
    def observed_lock(path, **options):
        if path.name == "job-start.lock" and not options["exclusive"]:
            attempted.set()
        return original_lock(path, **options)
    monkeypatch.setattr(LibraryMaterializer, "rebuild", paused_rebuild)
    monkeypatch.setattr(maintenance, "file_lock", observed_lock)
    rebuild = asyncio.create_task(client.post("/api/v1/library/rebuild"))
    claim = None
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        claim = asyncio.create_task(client.post(f"/api/v1/jobs/{job_id}/claim"))
        assert await asyncio.to_thread(attempted.wait, 3)
        assert not claim.done()
        with SessionLocal() as session:
            assert session.get(Job, job_id).state == JobState.QUEUED
    finally:
        release.set()
        rebuilt = await rebuild
        claimed = await claim if claim else None
    assert rebuilt.status_code == 200, rebuilt.text
    assert claimed is not None and claimed.status_code == 200


async def upload(client, run_id, name, role="source", parent=None):
    data = {"role": role}
    if parent:
        data["parent_artifact_id"] = parent
    response = await client.post(
        f"/api/v1/runs/{run_id}/artifacts/upload",
        files={"file": (name, name.encode())}, data=data,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def extract(client, artifact, count, **overrides):
    response = await client.post(f"/api/v1/artifacts/{artifact['id']}/extraction-results", json={
        "schema_version": "1.0", "extractor": "correctness-fixture", "extractor_version": "1",
        "result_type": "metadata", "payload": {"qc_summary": {"spectrum_count": count}}, **overrides,
    })
    assert response.status_code == 201, response.text
    return response.json()


async def test_source_observations_survive_later_derivatives_and_non_metadata_results(client, hierarchy):
    run_id = hierarchy["run_id"]
    source = await upload(client, run_id, "source.raw")
    converted = await upload(client, run_id, "converted.mzML", "derived", source["id"])
    filtered = await upload(client, run_id, "filtered.mgf", "derived", source["id"])
    await extract(client, source, 100)
    await extract(client, converted, 80)
    await extract(client, filtered, 40)
    await extract(client, source, 7, result_type="analysis")
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    qc = (await client.get(f"/api/v1/runs/{run_id}/qc")).json()
    assert run["spectraCount"] == qc["qc_summary"]["spectrum_count"] == 100
    assert run["latest_extraction"]["artifact_id"] == source["id"]
    assert run["summary_basis"]["reason"] == "source"
    assert qc["artifact_id"] == source["id"]
    with SessionLocal() as session:
        assert "spectra_count" not in session.get(Run, run_id).metadata_json
    manifest = (await client.get(f"/api/v1/runs/{run_id}/manifest")).json()
    assert manifest["run"]["metadata"]["spectra_count"] == 100
    assert manifest["run"]["summary_basis"]["artifact_id"] == source["id"]
    events = (await client.get("/api/v1/events/outbox")).json()
    source_events = [event for event in events if event["aggregate_id"] == source["id"] and event["topic"] == "artifact.metadata_extracted"]
    assert {event["payload"]["result_type"] for event in source_events} == {"metadata", "analysis"}


async def test_fallback_requires_linked_open_format_and_yields_to_source_metadata(client, hierarchy):
    run_id = hierarchy["run_id"]
    source = await upload(client, run_id, "source.raw")
    mgf = await upload(client, run_id, "filtered.mgf", "derived", source["id"])
    unrelated = await upload(client, run_id, "unlinked.mzML", "derived")
    await extract(client, mgf, 40)
    await extract(client, unrelated, 90)
    with SessionLocal() as session:
        session.get(Run, run_id).metadata_json = {"spectra_count": 40, "note": "Keep me"}
        session.commit()
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["spectraCount"] is None
    assert run["latest_extraction"] is None
    assert run["metadata_json"] == {"note": "Keep me"}
    assert (await client.get(f"/api/v1/runs/{run_id}/qc")).status_code == 404
    converted = await upload(client, run_id, "linked.mzML", "derived", source["id"])
    await extract(client, converted, 80)
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["spectraCount"] == 80
    assert run["summary_basis"]["reason"] == "linked_open_format_fallback"
    assert run["summary_basis"]["artifact_id"] == converted["id"]
    await extract(client, source, 100)
    await extract(client, converted, 50)
    assert (await client.get(f"/api/v1/runs/{run_id}")).json()["spectraCount"] == 100
    # Re-extraction replaces a whole observation, including unknown fields.
    await extract(client, source, 0)
    assert (await client.get(f"/api/v1/runs/{run_id}")).json()["spectraCount"] == 0
    await extract(client, source, None, payload={"qc_summary": {"ms2_count": 0}})
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["spectraCount"] is None
    assert run["ms2Count"] == 0


async def test_unknown_acquisition_time_stays_unknown_in_views_manifests_and_naming(client, hierarchy):
    run_id = hierarchy["run_id"]
    run = (await client.get(f"/api/v1/runs/{run_id}")).json()
    assert run["acquiredAt"] is None
    assert run["importedAt"]
    for route in ("/api/v1/runs", "/api/v1/overview"):
        data = (await client.get(route)).json()
        rows = data["runs"] if isinstance(data, dict) else data
        assert next(row for row in rows if row["id"] == run_id)["acquiredAt"] is None
    artifact = await upload(client, run_id, "date.mzML")
    manifest = (await client.get(f"/api/v1/runs/{run_id}/manifest")).json()
    assert manifest["run"]["acquired_at"] is None
    storage = LocalArtifactStorage(get_settings().storage_root, filename_template="{acquired_date}__{imported_date}{extension}")
    with SessionLocal() as session:
        run = session.get(Run, run_id)
        name = LibraryMaterializer(storage).artifact_filename(run.artifacts[0])
        assert name == f"unknown__{run.created_at.date().isoformat()}.mzML"
    assert Path(storage.library / artifact["library_path"]).is_file()
    known = await client.post("/api/v1/runs", json={
        "experiment_id": hierarchy["experiment_id"], "name": "Known date",
        "acquired_at": "2020-03-04T10:30:00Z",
    })
    assert known.status_code == 201
    view = (await client.get(f"/api/v1/runs/{known.json()['id']}")).json()
    assert view["acquiredAt"].startswith("2020-03-04T10:30:00")


def tree(path):
    return {item.relative_to(path).as_posix(): item.read_bytes() for item in path.rglob("*") if item.is_file()}


@pytest.mark.parametrize("failure", ["copy", "manifest", "publish", "commit"])
async def test_failed_rename_preserves_current_library_and_database(client, hierarchy, monkeypatch, failure):
    await upload(client, hierarchy["run_id"], "first.mzML")
    await upload(client, hierarchy["run_id"], "second.mgf")
    storage = LocalArtifactStorage(get_settings().storage_root)
    before = tree(storage.library)
    original_name = (await client.get(f"/api/v1/projects/{hierarchy['project_id']}")).json()["name"]
    if failure == "copy":
        def fail_copy(*args, **kwargs):
            raise OSError("Simulated disk full while copying")
        monkeypatch.setattr(LocalArtifactStorage, "_link_or_copy", fail_copy)
    elif failure == "manifest":
        def fail_manifest(*args, **kwargs):
            raise OSError("Simulated manifest failure")
        monkeypatch.setattr(LibraryMaterializer, "write_catalog", fail_manifest)
    elif failure == "publish":
        original = LibraryPublication.replace
        def fail_publish(self, source, destination):
            if source.name.endswith(".next"):
                raise OSError("Simulated publication failure")
            original(self, source, destination)
        monkeypatch.setattr(LibraryPublication, "replace", fail_publish)
    else:
        original = Session.commit
        def fail_commit(self):
            if self.scalar(select(AuditLog.id).where(AuditLog.action == "library.rebuilt")):
                raise OSError("Simulated database commit failure")
            original(self)
        monkeypatch.setattr(Session, "commit", fail_commit)
    response = await client.patch(f"/api/v1/projects/{hierarchy['project_id']}", json={"name": "Renamed"})
    assert response.status_code == 500, response.text
    assert tree(storage.library) == before
    assert not publication_journal(storage.library).exists()
    with SessionLocal() as session:
        assert session.get(Project, hierarchy["project_id"]).name == original_name
        assert all(storage.resolve_library(artifact.library_path).exists() for artifact in session.get(Run, hierarchy["run_id"]).artifacts)


@pytest.mark.parametrize("boundary", ["build", "old_moved", "new_published", "committed", "committed_missing"])
async def test_interrupted_rebuild_recovers_using_database_commit_witness(client, hierarchy, monkeypatch, boundary):
    await upload(client, hierarchy["run_id"], "recovery.mzML")
    storage = LocalArtifactStorage(get_settings().storage_root)
    before = tree(storage.library)
    class Interrupted(BaseException):
        pass
    if boundary == "build":
        def interrupt_build(*args, **kwargs):
            raise Interrupted()
        monkeypatch.setattr(LibraryMaterializer, "write_catalog", interrupt_build)
    elif boundary.startswith("committed"):
        original = LibraryPublication.commit
        def interrupt_commit(self, *args):
            original(self, *args)
            raise Interrupted()
        monkeypatch.setattr(LibraryPublication, "commit", interrupt_commit)
    else:
        original = LibraryPublication.replace
        def interrupt_replace(self, source, destination):
            original(self, source, destination)
            if boundary == "old_moved" and destination.name.endswith(".previous") or boundary == "new_published" and source.name.endswith(".next"):
                raise Interrupted()
        monkeypatch.setattr(LibraryPublication, "replace", interrupt_replace)
    with SessionLocal() as session:
        session.get(Project, hierarchy["project_id"]).name = "Recovered name"
        with pytest.raises(Interrupted):
            LibraryMaterializer(storage).rebuild(session)
    monkeypatch.undo()
    assert publication_journal(storage.library).exists()
    if boundary == "committed_missing":
        (storage.library / ".spectarr-generation").unlink()
        with pytest.raises(FileNotFoundError, match="generation is missing"):
            await client.get(f"/api/v1/projects/{hierarchy['project_id']}")
        journal = json.loads(publication_journal(storage.library).read_text())
        _, previous = LibraryPublication(storage).paths(journal["token"])
        assert tree(previous) == before
        return
    # API requests also recover an interrupted publication before reading it.
    response = await client.get(f"/api/v1/projects/{hierarchy['project_id']}")
    assert response.status_code == 200
    assert response.json()["name"] == ("Recovered name" if boundary == "committed" else "Proteomics")
    assert not publication_journal(storage.library).exists()
    if boundary != "committed":
        assert tree(storage.library) == before
    else:
        with SessionLocal() as session:
            artifact = session.get(Run, hierarchy["run_id"]).artifacts[0]
            assert storage.resolve_library(artifact.library_path).read_bytes() == b"recovery.mzML"
            assert artifact.library_path.startswith("recovered-name")
        catalog = json.loads((storage.library / "spectarr-library.json").read_text())
        assert catalog["projects"][0]["name"] == "Recovered name"


async def test_rebuild_requires_exclusive_access_and_preserves_copied_bundle(client, hierarchy, import_root):
    settings = get_settings()
    with maintenance_lock(settings.storage_root, exclusive=False):
        assert (await client.post("/api/v1/library/rebuild")).status_code == 503
        assert (await client.patch(f"/api/v1/projects/{hierarchy['project_id']}", json={"name": "Busy"})).status_code == 503
    bundle = import_root / "correctness-bundle.d"
    bundle.mkdir()
    (bundle / "data.bin").write_bytes(b"bundle content")
    response = await client.post(f"/api/v1/runs/{hierarchy['run_id']}/artifacts/import", json={"source_path": str(bundle)})
    assert response.status_code == 201
    storage = LocalArtifactStorage(settings.storage_root, link_mode="copy")
    with SessionLocal() as session:
        counts = LibraryMaterializer(storage).rebuild(session)
        assert counts["copied"] == 1
        artifact = session.get(Run, hierarchy["run_id"]).artifacts[0]
        assert (storage.resolve_library(artifact.library_path) / "data.bin").read_bytes() == b"bundle content"


async def test_processing_activity_and_unready_conversion(client, hierarchy):
    from spectarr.database import SessionLocal
    from spectarr.models import Artifact, ArtifactState

    uploaded = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/artifacts/upload",
        files={"file": ("processing.mgf", b"BEGIN IONS\n100 50\nEND IONS\n")},
    )
    artifact = uploaded.json()
    queued = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/derivatives",
        json={"format": "mzML", "input_artifact_id": artifact["id"]},
    )
    assert queued.status_code == 202
    run = (await client.get(f"/api/v1/runs/{hierarchy['run_id']}")).json()
    job = next(row for row in run["processing_jobs"] if row["id"] == queued.json()["id"])
    assert job["state"] == "queued"
    assert job["input_artifact_id"] == artifact["id"]
    assert job["output_format"] == "mzML"
    with SessionLocal() as session:
        session.get(Artifact, artifact["id"]).state = ArtifactState.MISSING
        session.commit()
    rejected = await client.post(
        f"/api/v1/runs/{hierarchy['run_id']}/derivatives",
        json={"format": "mzML", "input_artifact_id": artifact["id"]},
    )
    assert rejected.status_code == 409
