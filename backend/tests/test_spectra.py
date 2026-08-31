from __future__ import annotations

from typing import Any

import httpx
import pytest
from httpx import AsyncClient
from spectarr.api import get_spectrum_reader
from spectarr.main import app
from spectarr.spectrum_reader import SpectrumReaderClient, SpectrumReaderError

pytestmark = pytest.mark.anyio


def spectrum_payload() -> dict[str, Any]:
    return {
        "schema": "spxtacular.spectrum",
        "schema_version": 1,
        "kind": "msn_spectrum",
        "arrays": {
            "mz": [100.0, 200.0],
            "intensity": [10.0, 25.0],
            "charge": None,
            "im": None,
            "iso_score": None,
        },
        "metadata": {
            "spectrum_type": "centroid",
            "scan_number": 7,
            "ms_level": 2,
            "native_id": "scan=7",
        },
    }


def catalog_payload() -> dict[str, Any]:
    return {
        "schema": "spectarr.spectrum-catalog",
        "schema_version": 1,
        "total": 1,
        "offset": 0,
        "limit": 25,
        "match_index": 0,
        "items": [
            {
                "index": 0,
                "native_id": "scan=7",
                "scan_number": 7,
                "ms_level": 2,
                "rt": 90.0,
                "precursor_mz": 500.25,
                "precursor_charge": 2,
                "peak_count": 2,
                "total_ion_current": 35.0,
            }
        ],
    }


class FakeSpectrumReader:
    def __init__(self, error: SpectrumReaderError | None = None) -> None:
        self.error = error
        self.requests: list[dict[str, Any]] = []
        self.catalog_requests: list[dict[str, Any]] = []

    async def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        if self.error:
            raise self.error
        return spectrum_payload()

    async def catalog(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.catalog_requests.append(payload)
        if self.error:
            raise self.error
        return catalog_payload()


async def upload_mgf(client: AsyncClient, run_id: str) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/runs/{run_id}/artifacts/upload",
        files={"file": ("sample.mgf", b"BEGIN IONS\n100 10\nEND IONS\n")},
        data={"role": "source"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def override_reader(reader: FakeSpectrumReader):
    async def dependency() -> FakeSpectrumReader:
        return reader

    return dependency


async def test_spectrum_endpoint_proxies_versioned_transport(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    reader = FakeSpectrumReader()
    app.dependency_overrides[get_spectrum_reader] = override_reader(reader)
    try:
        response = await client.get(
            f"/api/v1/artifacts/{artifact['id']}/spectrum",
            params={"ms_level": 2, "index": 0},
        )
    finally:
        app.dependency_overrides.pop(get_spectrum_reader, None)
    assert response.status_code == 200, response.text
    assert response.json()["schema"] == "spxtacular.spectrum"
    assert response.json()["arrays"]["mz"] == [100.0, 200.0]
    assert reader.requests[0]["relative_path"].endswith(".mgf")
    assert reader.requests[0]["ms_level"] == 2
    assert reader.requests[0]["index"] == 0


async def test_spectrum_endpoint_defaults_to_first_position(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    reader = FakeSpectrumReader()
    app.dependency_overrides[get_spectrum_reader] = override_reader(reader)
    try:
        response = await client.get(
            f"/api/v1/artifacts/{artifact['id']}/spectrum", params={"ms_level": 2}
        )
    finally:
        app.dependency_overrides.pop(get_spectrum_reader, None)
    assert response.status_code == 200
    assert reader.requests[0]["index"] == 0


async def test_spectrum_catalog_endpoint_proxies_retention_time_search(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    reader = FakeSpectrumReader()
    app.dependency_overrides[get_spectrum_reader] = override_reader(reader)
    try:
        response = await client.get(
            f"/api/v1/artifacts/{artifact['id']}/spectra",
            params={"ms_level": 2, "rt_seconds": 90, "limit": 12},
        )
    finally:
        app.dependency_overrides.pop(get_spectrum_reader, None)
    assert response.status_code == 200, response.text
    assert response.json()["schema"] == "spectarr.spectrum-catalog"
    assert reader.catalog_requests[0]["relative_path"].endswith(".mgf")
    assert reader.catalog_requests[0]["rt_seconds"] == 90
    assert reader.catalog_requests[0]["limit"] == 12


async def test_persistent_catalog_filters_pages_and_reads_selected_row(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    started = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs",
        json={"extractor": "test", "extractor_version": "1", "schema_version": 1},
    )
    assert started.status_code == 201, started.text
    catalog_id = started.json()["id"]
    entries = [
        {
            "ordinal": ordinal,
            "ms_level_index": ordinal,
            "native_id": f"scan={scan}",
            "scan_number": scan,
            "ms_level": 2,
            "retention_time_seconds": 60.0 + ordinal,
            "precursor_mz": precursor,
            "precursor_charge": 2,
            "neutral_mass": precursor * 2 - 2.014552933242,
            "peak_count": 100 + ordinal,
            "total_ion_current": 1000.0 * (ordinal + 1),
            "base_peak_mz": 150.0 + ordinal,
        }
        for ordinal, (scan, precursor) in enumerate([(10, 400.2), (11, 500.2), (12, 600.2)])
    ]
    appended = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs/{catalog_id}/entries",
        json={"entries": entries},
    )
    assert appended.status_code == 202, appended.text
    completed = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs/{catalog_id}/complete",
        json={"spectrum_count": 3},
    )
    assert completed.status_code == 200, completed.text

    first = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectra/query",
        json={
            "ms_levels": [2],
            "precursor_mz_min": 450,
            "sort": "scan_number",
            "direction": "asc",
            "limit": 1,
        },
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["strategy"] == "persistent"
    assert body["total"] == 2
    assert body["items"][0]["scan_number"] == 11
    assert body["next_cursor"]

    second = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectra/query",
        json={
            "ms_levels": [2],
            "precursor_mz_min": 450,
            "sort": "scan_number",
            "direction": "asc",
            "cursor": body["next_cursor"],
            "limit": 1,
        },
    )
    assert second.status_code == 200, second.text
    assert second.json()["items"][0]["scan_number"] == 12

    reader = FakeSpectrumReader()
    app.dependency_overrides[get_spectrum_reader] = override_reader(reader)
    try:
        selected = await client.get(
            f"/api/v1/artifacts/{artifact['id']}/spectra/{body['items'][0]['id']}"
        )
    finally:
        app.dependency_overrides.pop(get_spectrum_reader, None)
    assert selected.status_code == 200, selected.text
    assert reader.requests[0]["native_id"] == "scan=11"
    assert selected.json()["metadata"]["scan_number"] == 11
    assert selected.json()["metadata"]["native_id"] == "scan=11"


async def test_persistent_catalog_rejects_invalid_completion_count(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    started = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs",
        json={"extractor": "test", "extractor_version": "1"},
    )
    catalog_id = started.json()["id"]
    response = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs/{catalog_id}/complete",
        json={"spectrum_count": 1},
    )
    assert response.status_code == 409
    status_response = await client.get(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalog"
    )
    assert status_response.json()["status"] == "building"


async def test_failed_catalog_reports_error_and_allows_clean_retry(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    started = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs",
        json={"extractor": "test", "extractor_version": "1"},
    )
    catalog_id = started.json()["id"]
    failed = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs/{catalog_id}/fail",
        json={"error": "Reader could not open this file"},
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["status"] == "failed"
    assert failed.json()["error"] == "Reader could not open this file"

    status_response = await client.get(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalog"
    )
    assert status_response.json()["status"] == "failed"
    retried = await client.post(
        f"/api/v1/artifacts/{artifact['id']}/spectrum-catalogs",
        json={"extractor": "retry", "extractor_version": "2"},
    )
    assert retried.status_code == 201, retried.text
    assert retried.json()["status"] == "building"


async def test_spectrum_endpoint_rejects_multiple_selectors(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    response = await client.get(
        f"/api/v1/artifacts/{artifact['id']}/spectrum",
        params={"ms_level": 2, "index": 0, "scan_number": 7},
    )
    assert response.status_code == 422
    assert "Choose only one" in response.json()["detail"]


async def test_spectrum_endpoint_preserves_reader_not_found(
    client: AsyncClient, hierarchy: dict[str, str]
) -> None:
    artifact = await upload_mgf(client, hierarchy["run_id"])
    reader = FakeSpectrumReader(
        SpectrumReaderError(404, "No matching spectrum was found")
    )
    app.dependency_overrides[get_spectrum_reader] = override_reader(reader)
    try:
        response = await client.get(
            f"/api/v1/artifacts/{artifact['id']}/spectrum",
            params={"ms_level": 2, "index": 99},
        )
    finally:
        app.dependency_overrides.pop(get_spectrum_reader, None)
    assert response.status_code == 404
    assert response.json()["detail"] == "No matching spectrum was found"


async def test_internal_reader_client_validates_transport_schema() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Spectarr-Worker-Token"] == "worker-token"
        return httpx.Response(200, json=spectrum_payload())

    reader = SpectrumReaderClient(
        "http://spectrum-reader:8002/",
        "worker-token",
        5,
        httpx.MockTransport(handler),
    )
    result = await reader.read({"relative_path": "library/sample.mgf"})
    assert result["metadata"]["scan_number"] == 7


async def test_internal_reader_client_validates_catalog_schema() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/spectra/catalog"
        return httpx.Response(200, json=catalog_payload())

    reader = SpectrumReaderClient(
        "http://spectrum-reader:8002",
        "worker-token",
        5,
        httpx.MockTransport(handler),
    )
    result = await reader.catalog({"relative_path": "library/sample.mgf"})
    assert result["items"][0]["native_id"] == "scan=7"


async def test_internal_reader_client_preserves_error_detail() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "Unsupported source format"})

    reader = SpectrumReaderClient(
        "http://spectrum-reader:8002",
        "worker-token",
        5,
        httpx.MockTransport(handler),
    )
    with pytest.raises(
        SpectrumReaderError, match="Unsupported source format"
    ) as raised:
        await reader.read({})
    assert raised.value.status == 422


@pytest.mark.parametrize(
    ("url", "token", "message"),
    [
        (None, "worker-token", "not configured"),
        ("http://spectrum-reader:8002", None, "authentication is not configured"),
    ],
)
async def test_internal_reader_client_requires_configuration(
    url: str | None, token: str | None, message: str
) -> None:
    reader = SpectrumReaderClient(url, token, 5)
    with pytest.raises(SpectrumReaderError, match=message):
        await reader.read({})


async def test_internal_reader_client_rejects_wrong_schema() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"schema": "other", "schema_version": 1})

    reader = SpectrumReaderClient(
        "http://spectrum-reader:8002",
        "worker-token",
        5,
        httpx.MockTransport(handler),
    )
    with pytest.raises(SpectrumReaderError, match="unsupported transport schema"):
        await reader.read({})
