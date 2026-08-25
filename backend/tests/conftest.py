from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


TEST_ROOT = Path(tempfile.mkdtemp(prefix="spectarr-tests-"))
IMPORT_ROOT = TEST_ROOT / "imports"
IMPORT_ROOT.mkdir()
os.environ["SPECTARR_DATABASE_URL"] = f"sqlite:///{TEST_ROOT / 'test.db'}"
os.environ["SPECTARR_STORAGE_ROOT"] = str(TEST_ROOT / "storage")
os.environ["SPECTARR_IMPORT_ROOTS"] = f'["{IMPORT_ROOT}"]'
os.environ["SPECTARR_AUTH_ENABLED"] = "false"

from spectarr.database import Base, engine  # noqa: E402
from spectarr.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as test_client:
        yield test_client


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def import_root() -> Path:
    return IMPORT_ROOT


@pytest.fixture
async def hierarchy(client: AsyncClient) -> dict[str, str]:
    project = (await client.post("/api/v1/projects", json={"name": "Proteomics"})).json()
    experiment = (await client.post(
        "/api/v1/experiments",
        json={"project_id": project["id"], "name": "Benchmark"},
    )).json()
    sample = (await client.post(
        "/api/v1/samples",
        json={"experiment_id": experiment["id"], "name": "HeLa"},
    )).json()
    run = (await client.post(
        "/api/v1/runs",
        json={
            "experiment_id": experiment["id"],
            "sample_id": sample["id"],
            "name": "hela-01",
            "source_class": "open",
        },
    )).json()
    return {
        "project_id": project["id"],
        "experiment_id": experiment["id"],
        "sample_id": sample["id"],
        "run_id": run["id"],
    }
