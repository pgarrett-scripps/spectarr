from __future__ import annotations

import io
import subprocess
import zipfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from spectarr.sdrf import official_validation


pytestmark = pytest.mark.anyio


async def test_sdrf_hierarchy_fixture(client: AsyncClient, hierarchy: dict[str, str]) -> None:
    assert hierarchy["run_id"]


async def test_multiplexed_run_and_generated_sdrf_round_trip(client: AsyncClient) -> None:
    project = (await client.post(
        "/api/v1/projects",
        json={"name": "Labeled study", "metadata_json": {"keywords": ["proteomics"]}},
    )).json()
    experiment = (await client.post(
        "/api/v1/experiments",
        json={"project_id": project["id"], "name": "TMT experiment"},
    )).json()
    samples = []
    for name, replicate in [("control", "1"), ("treated", "2")]:
        samples.append((await client.post(
            "/api/v1/samples",
            json={
                "experiment_id": experiment["id"],
                "name": name,
                "metadata_json": {
                    "organism": "Homo sapiens",
                    "organism_part": "cell",
                    "disease": "normal",
                    "biological_replicate": replicate,
                },
            },
        )).json())
    run_response = await client.post(
        "/api/v1/runs",
        json={
            "experiment_id": experiment["id"],
            "name": "plex-01",
            "source_class": "vendor",
            "samples": [
                {"sample_id": samples[0]["id"], "label": "TMT126"},
                {"sample_id": samples[1]["id"], "label": "TMT127N"},
            ],
        },
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    assert [link["label"] for link in run["sample_links"]] == ["TMT126", "TMT127N"]

    upload = await client.post(
        f"/api/v1/runs/{run['id']}/artifacts/upload",
        files={"file": ("plex-01.raw", b"vendor bytes")},
        data={"role": "source"},
    )
    assert upload.status_code == 201, upload.text

    generated = await client.post(f"/api/v1/projects/{project['id']}/sdrf/generate")
    assert generated.status_code == 200, generated.text
    document = generated.json()
    assert document["columns"].count("comment[sdrf template]") == 1
    assert len(document["rows"]) == 2
    label_column = document["columns"].index("comment[label]")
    assert [row["values"][label_column] for row in document["rows"]] == ["TMT126", "TMT127N"]
    assert all(row["run_id"] == run["id"] for row in document["rows"])

    exported = await client.get(f"/api/v1/projects/{project['id']}/sdrf/export")
    assert exported.status_code == 200
    assert exported.content.splitlines()[0].decode().split("\t") == document["columns"]


async def test_duplicate_legal_columns_import_validation_and_submission_package(
    client: AsyncClient,
) -> None:
    project = (await client.post("/api/v1/projects", json={"name": "Submission study"})).json()
    experiment = (await client.post(
        "/api/v1/experiments", json={"project_id": project["id"], "name": "Experiment"}
    )).json()
    sample = (await client.post(
        "/api/v1/samples", json={"experiment_id": experiment["id"], "name": "HeLa"}
    )).json()
    run = (await client.post(
        "/api/v1/runs",
        json={"experiment_id": experiment["id"], "sample_id": sample["id"], "name": "hela-01"},
    )).json()
    upload = await client.post(
        f"/api/v1/runs/{run['id']}/artifacts/upload",
        files={"file": ("fixture.mzML", b"minimal mzML")},
        data={"role": "source"},
    )
    assert upload.status_code == 201, upload.text
    artifact = upload.json()

    generated = (await client.post(
        f"/api/v1/projects/{project['id']}/sdrf/generate"
    )).json()
    columns = [*generated["columns"], "comment[sdrf template]", "comment[associated data file]"]
    values = [*generated["rows"][0]["values"], "human v1.1.0", "not applicable"]
    text = "\ufeff" + "\t".join(columns) + "\n" + "\t".join(values) + "\n"
    imported = await client.post(
        f"/api/v1/projects/{project['id']}/sdrf/import",
        files={"file": ("study.sdrf.tsv", text.encode(), "text/tab-separated-values")},
        data={"synchronize": "true"},
    )
    assert imported.status_code == 200, imported.text
    document = imported.json()
    assert document["columns"].count("comment[sdrf template]") == 2
    assert document["rows"][0]["artifact_id"] == artifact["id"]

    saved = await client.put(
        f"/api/v1/projects/{project['id']}/sdrf",
        json={
            "columns": document["columns"],
            "rows": [{"values": document["rows"][0]["values"]}],
            "templates": ["ms-proteomics v1.1.0", "human v1.1.0"],
            "source_filename": "study.sdrf.tsv",
            "synchronize": True,
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["revision"] == document["revision"] + 1

    validation = await client.post(
        f"/api/v1/projects/{project['id']}/sdrf/validate",
        json={"ontology": False},
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["valid"] is True
    project_manifest = (await client.get(
        f"/api/v1/projects/{project['id']}/manifest"
    )).json()
    assert project_manifest["sdrf"]["status"] == "valid"
    assert project_manifest["sdrf"]["revision"] == saved.json()["revision"]

    preview = (await client.get(
        f"/api/v1/projects/{project['id']}/submission/preview"
    )).json()
    assert preview["ready"] is True
    assert preview["mapped_rows"] == 1

    package = await client.get(f"/api/v1/projects/{project['id']}/submission/export")
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert "study.sdrf.tsv" in archive.namelist()
        assert "files/hela-01__HeLa__" in "\n".join(archive.namelist())
        assert "checksums.sha256" in archive.namelist()
        assert "spectarr-submission.json" in archive.namelist()


async def test_sdrf_rejects_missing_required_columns(client: AsyncClient, hierarchy: dict[str, str]) -> None:
    imported = await client.post(
        f"/api/v1/projects/{hierarchy['project_id']}/sdrf/import",
        files={"file": ("bad.sdrf.tsv", b"source name\tassay name\nHeLa\trun\n")},
    )
    assert imported.status_code == 200
    validation = await client.post(
        f"/api/v1/projects/{hierarchy['project_id']}/sdrf/validate",
        json={"ontology": False},
    )
    assert validation.status_code == 200
    assert validation.json()["valid"] is False
    assert any(message["code"] == "missing_column" for message in validation.json()["messages"])


async def test_official_validator_reports_each_error_separately() -> None:
    document = SimpleNamespace(
        columns=["source name"],
        rows=[SimpleNamespace(values=["sample-1"])],
        templates=["ms-proteomics v1.1.0"],
    )
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=1,
        stdout="ERROR: Missing organism\nERROR: Missing instrument\nThere were validation errors.\n",
        stderr="",
    )
    with patch("spectarr.sdrf.shutil.which", return_value="/usr/bin/parse_sdrf"), patch(
        "spectarr.sdrf.subprocess.run", return_value=completed
    ):
        result = official_validation(document, ontology=False)
    assert [item["message"] for item in result["messages"]] == ["Missing organism", "Missing instrument"]
