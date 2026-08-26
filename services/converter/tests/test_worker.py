from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

from spectarr_converter.models import ConversionResult, OutputArtifact
from spectarr_converter.worker import ApiConversionWorker


class FakeConverter:
    def __init__(self, output: Path) -> None:
        self.output = output

    def convert_with_control(self, conversion_request, cancel_event=None, progress=None):
        if progress:
            progress("finished", 1.0)
        return ConversionResult(
            job_id=conversion_request.job_id,
            status="succeeded",
            recipe=conversion_request.recipe,
            image="pwiz:1",
            started_at="start",
            finished_at="finish",
            duration_seconds=1.0,
            converter_version="1.2.0",
            command=["docker", "run", "pwiz:1"],
            outputs=[OutputArtifact(str(self.output), "mzML", self.output.stat().st_size, "a" * 64)],
            scratch_path=str(self.output.parent),
        )


class FakeApi:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.patches: list[dict[str, Any]] = []
        self.current_state = "running"

    def get(self, path: str, query=None):
        if path == "/api/v1/jobs":
            return [{"id": "job-1", "kind": "convert"}]
        if path == "/api/v1/jobs/job-1":
            return {"id": "job-1", "state": self.current_state}
        if path.endswith("/location"):
            return {
                "path": str(self.source),
                "relative_path": "objects/sha256/aa/source",
                "filename": "source.raw",
            }
        if path.startswith("/api/v1/artifacts/"):
            return {"id": "artifact-1", "run_id": "run-1"}
        if path.startswith("/api/v1/recipes/"):
            return {
                "id": "recipe-1",
                "name": "archival-mzml-v1",
                "converter": "msconvert",
                "converter_version": None,
                "output_format": "mzML",
                "parameters": {},
            }
        raise AssertionError(path)

    def post(self, path: str, payload=None):
        self.assert_path = path
        return {
            "id": "job-1",
            "kind": "convert",
            "input_artifact_id": "artifact-1",
            "recipe_id": "recipe-1",
            "attempts": 1,
            "parameters": {"recipe_fingerprint": "f" * 64},
        }

    def patch(self, path: str, payload: dict[str, Any]):
        self.patches.append(payload)
        return payload

    def upload_artifact(self, path: str, file_path: Path, fields: dict[str, str]):
        self.upload = (path, file_path, fields)
        return {"id": "output-1"}


class WorkerTests(unittest.TestCase):
    def test_claims_converts_uploads_and_completes_job(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.raw"
            source.write_bytes(b"raw")
            scratch = root / "job"
            scratch.mkdir()
            output = scratch / "source.mzML"
            output.write_text("<mzML></mzML>")
            api = FakeApi(source)
            worker = ApiConversionWorker(api, FakeConverter(output))
            self.assertTrue(worker.process_one())
            self.assertEqual(api.upload[0], "/api/v1/runs/run-1/artifacts/upload")
            self.assertEqual(api.upload[2]["parent_artifact_id"], "artifact-1")
            self.assertEqual(api.upload[2]["expected_sha256"], "a" * 64)
            self.assertEqual(api.patches[-1]["state"], "succeeded")
            self.assertEqual(api.patches[-1]["output_artifact_id"], "output-1")
            metadata = __import__("json").loads(api.upload[2]["metadata_json"])
            self.assertEqual(metadata["converter_library_version"], "1.2.0")
            self.assertEqual(metadata["converter_command"], ["docker", "run", "pwiz:1"])
            self.assertEqual(metadata["container_format"], "plain")

    def test_unknown_recipe_marks_job_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.raw"
            source.write_bytes(b"raw")
            output = root / "source.mzML"
            output.write_text("<mzML></mzML>")
            api = FakeApi(source)
            original_get = api.get

            def get(path: str, query=None):
                if path.startswith("/api/v1/recipes/"):
                    return {"id": "recipe-1", "name": "custom-shell-args", "converter": "shell"}
                return original_get(path, query)

            api.get = get
            ApiConversionWorker(api, FakeConverter(output)).process_one()
            self.assertEqual(api.patches[-1]["state"], "failed")
            self.assertIn("Only the msconvert converter", api.patches[-1]["error"])

    def test_maps_api_location_to_host_storage_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "objects" / "sha256" / "aa" / "source"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"raw")
            output = root / "source.mzML"
            output.write_text("<mzML></mzML>")
            api = FakeApi(source)
            worker = ApiConversionWorker(api, FakeConverter(output), local_storage_root=root)
            location = {"path": "/data/storage/objects/sha256/aa/source", "relative_path": "objects/sha256/aa/source"}
            self.assertEqual(worker._resolve_worker_source(location), str(source))

    def test_rejects_storage_relative_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            worker = ApiConversionWorker(
                FakeApi(Path(temporary)),
                FakeConverter(Path(temporary) / "missing"),
                local_storage_root=Path(temporary),
            )
            with self.assertRaisesRegex(ValueError, "escapes"):
                worker._resolve_worker_source({"path": "/tmp/escape", "relative_path": "../../escape"})

    def test_run_forever_retries_temporary_api_outage(self) -> None:
        worker = ApiConversionWorker(Mock(), Mock())
        worker.process_one = Mock(
            side_effect=[
                RuntimeError("Spectarr API is unavailable: connection refused"),
                KeyboardInterrupt(),
            ]
        )
        with patch("spectarr_converter.worker.time.sleep") as sleep:
            with self.assertRaises(KeyboardInterrupt):
                worker.run_forever(0.5)
        sleep.assert_called_once_with(1.0)

    def test_run_forever_does_not_hide_non_connection_errors(self) -> None:
        worker = ApiConversionWorker(Mock(), Mock())
        worker.process_one = Mock(side_effect=RuntimeError("invalid worker token"))
        with self.assertRaisesRegex(RuntimeError, "invalid worker token"):
            worker.run_forever(0.5)


if __name__ == "__main__":
    unittest.main()
