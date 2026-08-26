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


class FakeSpectrumReader:
    def __init__(self, error: SpectrumReaderError | None = None) -> None:
        self.error = error
        self.requests: list[dict[str, Any]] = []

    async def read(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(payload)
        if self.error:
            raise self.error
        return spectrum_payload()


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
