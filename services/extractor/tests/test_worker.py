from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from spectarr_extractor.models import ExtractionResult, SpectrumObservation
from spectarr_extractor.worker import LeaseHeartbeat, MetadataExtractionWorker


class FakeProviders:
    def extract(self, path: Path, declared_format: str | None = None, on_spectrum=None, on_provider_start=None) -> ExtractionResult:
        if on_provider_start:
            on_provider_start("test-parser", "1.2.3")
        if on_spectrum:
            on_spectrum(SpectrumObservation(ordinal=0, ms_level_index=0, native_id="scan=1", scan_number=1))
        return ExtractionResult(
            parser_provider="test-parser",
            parser_version="1.2.3",
            source_format=declared_format or "mzML",
            metadata={"path": path.name},
            qc_summary={"spectrum_count": 1},
            warnings=["test warning"],
        )


class FakeApi:
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.posts: list[tuple[str, Any]] = []
        self.patches: list[tuple[str, Any]] = []

    def get(self, path: str, query=None):
        if path == "/api/v1/jobs":
            return [{"id": "job-1", "kind": "extract_metadata"}]
        if path.endswith("/location"):
            return {"relative_path": self.relative_path, "filename": "sample.mzML"}
        if path.startswith("/api/v1/artifacts/"):
            return {"id": "artifact-1", "format": "mzML"}
        raise AssertionError(path)

    def post(self, path: str, payload=None):
        self.posts.append((path, payload))
        if path.endswith("/claim"):
            return {"id": "job-1", "input_artifact_id": "artifact-1"}
        if path.endswith("/spectrum-catalogs"):
            return {"id": "catalog-1"}
        return {"id": "result-1"}

    def patch(self, path: str, payload: dict[str, Any]):
        self.patches.append((path, payload))
        return payload


class WorkerTests(unittest.TestCase):
    def test_extracts_posts_versioned_result_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "objects" / "sample"
            source.parent.mkdir()
            source.write_text("data")
            api = FakeApi("objects/sample")
            worker = MetadataExtractionWorker(api, FakeProviders(), root, heartbeat_seconds=60)
            self.assertTrue(worker.process_one())
            result_post = next(value for value in api.posts if value[0].endswith("/extraction-results"))
            self.assertEqual(result_post[1]["schema_version"], "1.0")
            self.assertEqual(result_post[1]["extractor"], "test-parser")
            self.assertEqual(result_post[1]["payload"]["qc_summary"]["spectrum_count"], 1)
            self.assertEqual(result_post[1]["payload"]["spectrum_count"], 1)
            catalog_batch = next(value for value in api.posts if value[0].endswith("/entries"))
            self.assertEqual(catalog_batch[1]["entries"][0]["scan_number"], 1)
            self.assertTrue(any(path.endswith("/complete") for path, _ in api.posts))
            self.assertEqual(api.patches[-1][1], {"state": "succeeded", "progress": 1.0})

    def test_rejects_relative_path_escape_and_fails_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            api = FakeApi("../../escape")
            worker = MetadataExtractionWorker(api, FakeProviders(), Path(temporary))
            worker.process_one()
            self.assertEqual(api.patches[-1][1]["state"], "failed")
            self.assertIn("escapes", api.patches[-1][1]["error"])

    def test_heartbeat_renews_lease(self) -> None:
        api = FakeApi("unused")
        with LeaseHeartbeat(api, "job-1", interval_seconds=0.01):
            time.sleep(0.025)
        heartbeats = [path for path, _ in api.posts if path.endswith("/heartbeat")]
        self.assertGreaterEqual(len(heartbeats), 1)


if __name__ == "__main__":
    unittest.main()
