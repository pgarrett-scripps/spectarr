from __future__ import annotations

import gzip
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mzmlpy import AccessStrategy, Mzml
from mzmlpy.embedded_indexed_gzip import is_embedded_indexed_gzip

from spectarr_converter.models import ConversionRequest
from spectarr_converter.recipes import Recipe, get_recipe
from spectarr_converter.service import ConversionService, MsconvertCliRunner, ProcessReport


class FakeRunner:
    def __init__(
        self,
        content: bytes,
        suffix: str = ".mzML",
        return_code: int = 0,
        cancelled: bool = False,
        stderr: str = "",
    ) -> None:
        self.content = content
        self.suffix = suffix
        self.return_code = return_code
        self.cancelled = cancelled
        self.stderr = stderr
        self.recipe: Recipe | None = None

    def run(
        self,
        source: Path,
        output_dir: Path,
        recipe: Recipe,
        image: str,
        source_name: str | None = None,
        cancel_event=None,
        progress=None,
    ) -> ProcessReport:
        self.recipe = recipe
        (output_dir / f"{source.stem}{self.suffix}").write_bytes(self.content)
        return ProcessReport(
            self.return_code,
            "converter output",
            self.stderr,
            ("docker", "run", image),
            "1.2.0",
            self.cancelled,
        )


class ConversionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source_root = self.root / "sources"
        self.source_root.mkdir()
        self.source = self.source_root / "sample.raw"
        self.source.write_bytes(b"raw")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_returns_validated_structured_result(self) -> None:
        runner = FakeRunner(b'<?xml version="1.0"?><mzML></mzML>')
        service = ConversionService(self.root / "scratch", (self.source_root,), runner=runner)
        result = service.convert(ConversionRequest("job-1", str(self.source), "archival-mzml-v1"))
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.outputs[0].format, "mzML")
        output = Path(result.outputs[0].path)
        self.assertTrue(output.name.endswith(".mzML.gz"))
        self.assertTrue(is_embedded_indexed_gzip(output))
        with gzip.open(output, "rb") as stream:
            self.assertEqual(stream.read(), b'<?xml version="1.0"?><mzML></mzML>')
        with Mzml(output, in_memory=False) as reader:
            self.assertEqual(reader.access_strategy, AccessStrategy.EMBEDDED)
        self.assertEqual(len(result.outputs[0].sha256), 64)
        self.assertEqual(result.converter_version, "1.2.0")
        self.assertEqual(result.command, ["docker", "run", result.image])

    def test_rejects_source_outside_allowlist(self) -> None:
        outside = self.root / "outside.raw"
        outside.write_bytes(b"raw")
        service = ConversionService(self.root / "scratch", (self.source_root,), runner=FakeRunner(b""))
        result = service.convert(ConversionRequest("job-2", str(outside), "archival-mzml-v1"))
        self.assertEqual(result.status, "failed")
        self.assertIn("outside configured storage roots", result.error or "")

    def test_rejects_unpinned_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "pinned"):
            ConversionService(self.root / "scratch", (self.source_root,), image="pwiz:latest")

    def test_failed_validation_is_not_published_as_output(self) -> None:
        service = ConversionService(
            self.root / "scratch",
            (self.source_root,),
            runner=FakeRunner(b"not xml"),
        )
        result = service.convert(ConversionRequest("job-3", str(self.source), "archival-mzml-v1"))
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.outputs, [])
        self.assertIn("Invalid mzML", result.error or "")

    def test_failed_converter_includes_stderr_in_error(self) -> None:
        service = ConversionService(
            self.root / "scratch",
            (self.source_root,),
            runner=FakeRunner(b"", return_code=1, stderr="invalid filter syntax"),
        )
        result = service.convert(ConversionRequest("job-failed", str(self.source), "archival-mzml-v1"))
        self.assertEqual(result.status, "failed")
        self.assertIn("invalid filter syntax", result.error or "")

    def test_cancellation_is_reported_and_scratch_is_removed(self) -> None:
        cancel = threading.Event()
        cancel.set()
        service = ConversionService(
            self.root / "scratch",
            (self.source_root,),
            runner=FakeRunner(b"", cancelled=True),
        )
        result = service.convert_with_control(
            ConversionRequest("job-cancel", str(self.source), "archival-mzml-v1"),
            cancel,
        )
        self.assertEqual(result.status, "cancelled")
        self.assertIsNone(result.scratch_path)

    def test_input_mounts_are_read_only(self) -> None:
        command = [
            "docker",
            "run",
            "-v",
            "/data:/input",
            "-v",
            "/tmp/out:/output",
            "image:1",
        ]
        rewritten = MsconvertCliRunner._make_input_mounts_read_only(command)
        self.assertIn("/data:/input:ro", rewritten)
        self.assertIn("/tmp/out:/output", rewritten)

    def test_content_addressed_file_can_keep_original_name_in_container(self) -> None:
        command = ["docker", "run", "-v", "/store/ab:/input", "image:1", "/input/hash"]
        rewritten = MsconvertCliRunner._alias_file_mount(command, Path("/store/ab/hash"), "sample.raw")
        self.assertIn("/store/ab/hash:/input/sample.raw", rewritten)
        self.assertIn("/input/sample.raw", rewritten)

    def test_maps_worker_paths_for_host_docker_daemon(self) -> None:
        runner = MsconvertCliRunner(Path("/data"), Path("/host/project/data"))
        command = ["docker", "run", "-v", "/data/storage/hash:/input/sample.raw", "image:1"]
        rewritten = runner._map_docker_mount_sources(command)
        self.assertIn("/host/project/data/storage/hash:/input/sample.raw", rewritten)

    def test_maps_worker_paths_with_the_most_specific_mount(self) -> None:
        runner = MsconvertCliRunner(
            mount_map={"/data": "/host/data", "/data/storage": "/tank/spectarr"}
        )
        command = [
            "docker",
            "run",
            "-v",
            "/data/storage/hash:/input/sample.raw",
            "-v",
            "/data/scratch/job:/output",
            "image:1",
        ]
        rewritten = runner._map_docker_mount_sources(command)
        self.assertIn("/tank/spectarr/hash:/input/sample.raw", rewritten)
        self.assertIn("/host/data/scratch/job:/output", rewritten)

    def test_mount_map_is_read_from_the_environment(self) -> None:
        environment = {"SPECTARR_DOCKER_MOUNT_MAP": '{"/data": "/host/data", "/data/storage": "/tank/s"}'}
        with patch.dict(os.environ, environment, clear=False):
            runner = MsconvertCliRunner()
        command = ["docker", "run", "-v", "/data/storage/hash:/input/a.raw", "image:1"]
        self.assertIn("/tank/s/hash:/input/a.raw", runner._map_docker_mount_sources(command))

    def test_runner_names_conversion_container_for_cancellation(self) -> None:
        output_dir = self.root / "output"
        output_dir.mkdir()
        converter = Mock()
        converter.build_docker_command.return_value = ["docker", "run", "image:1"]
        converter.execute_command.return_value = SimpleNamespace(
            return_code=0,
            stdout="",
            stderr="",
            command=("docker", "run", "image:1"),
            tool_version="1.2.0",
            cancelled=False,
        )
        with patch("msconvert_cli.converter.SimplePWizConverter", return_value=converter):
            MsconvertCliRunner().run(
                self.source,
                output_dir,
                get_recipe("archival-mzml-v1"),
                "pwiz:1",
            )
        container_name = converter.build_docker_command.call_args.kwargs["container_name"]
        self.assertTrue(container_name.startswith("spectarr-msconvert-"))
        self.assertEqual(converter.execute_command.call_args.kwargs["container_name"], container_name)

    def test_named_config_is_copied_into_the_shared_job_directory(self) -> None:
        config = self.root / "sage.txt"
        config.write_text("mzML=true\n")
        runner = FakeRunner(b'<?xml version="1.0"?><mzML></mzML>')
        service = ConversionService(self.root / "scratch", (self.source_root,), runner=runner)
        named = Recipe("Sage", 1, get_recipe("archival-mzml-v1").output_format, (), config)
        with patch("spectarr_converter.service.compile_recipe", return_value=named):
            result = service.convert(
                ConversionRequest(
                    "job-config",
                    str(self.source),
                    "sage",
                    recipe_definition={"name": "Sage"},
                )
            )
        self.assertEqual(result.status, "succeeded")
        self.assertIsNotNone(runner.recipe)
        runtime_config = result.scratch_path and Path(result.scratch_path) / "msconvert-config.txt"
        self.assertEqual(runner.recipe.config_path, runtime_config)


if __name__ == "__main__":
    unittest.main()
