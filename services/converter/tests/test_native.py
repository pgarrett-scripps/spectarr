from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from spectarr_converter.models import ConversionRequest
from spectarr_converter.native import NativeMsconvertRunner
from spectarr_converter.recipes import compile_recipe
from spectarr_converter.service import ConversionService


def definition(output="MGF", **parameters):
    return {
        "name": "native-test",
        "converter": "msconvert-rs",
        "output_format": output,
        "parameters": parameters,
    }


class NativeRecipeTests(unittest.TestCase):
    def test_loss_policy_is_compiled_and_never_silently_dropped(self):
        policy = {"deny": ["unqualified-calibration", "precision-reduction"], "require": ["polarity", "precursor-target", "collision-energy"]}
        recipe = compile_recipe(definition("mzML", loss_policy=policy))
        self.assertEqual(recipe.arguments.count("--forbid-loss"), 2)
        self.assertEqual(recipe.arguments.count("--require-field"), 3)
        for value in policy["deny"] + policy["require"]:
            self.assertIn(value, recipe.arguments)
        legacy = {**definition("mzML", loss_policy={"deny": [], "require": []}), "converter": "msconvert"}
        self.assertEqual(compile_recipe(legacy).converter, "msconvert")
        with self.assertRaisesRegex(ValueError, "explicit msconvert-rs"):
            compile_recipe(legacy, {"loss_policy": policy})
        for bad in (None, [], "polarity", {"require": "polarity"}, {"deny": [True]}, {"deny": ["unknown"]}, {"allow": []}, {"require": None}):
            for converter in ("msconvert", "msconvert-rs"):
                with self.subTest(policy=bad, converter=converter), self.assertRaises(ValueError):
                    compile_recipe({**definition("mzML", loss_policy=bad), "converter": converter})

    def test_approximate_calibration_requires_explicit_experimental_consent(self):
        recipe = compile_recipe(definition("mzML", allow_experimental=True, allow_approximate_calibration=True))
        self.assertIn("--allow-approximate-calibration", recipe.arguments)
        self.assertIn("--allow-experimental", recipe.arguments)
        for params in ({"allow_approximate_calibration": True}, {"allow_approximate_calibration": "false", "allow_experimental": True}, {"allow_approximate_calibration": None}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                compile_recipe(definition("mzML", **params))
        with self.assertRaises(ValueError):
            compile_recipe({**definition("mzML", allow_experimental=True, allow_approximate_calibration=True), "converter": "msconvert"})

    def test_native_is_explicit_and_safe_controls_survive(self):
        recipe = compile_recipe(
            definition(
                "mzML",
                stored_centroids=True,
                require_retention_time=True,
                max_peaks_per_spectrum=1000,
            )
        )
        self.assertEqual(recipe.converter, "msconvert-rs")
        self.assertIn("--stored-centroids", recipe.arguments)
        self.assertIn("--require-retention-time", recipe.arguments)
        self.assertIn("--max-peaks-per-spectrum", recipe.arguments)
        self.assertIn("--inten64", recipe.arguments)
        recipe = compile_recipe(definition("MGF"))
        self.assertNotIn("--inten64", recipe.arguments)
        self.assertNotIn("--zlib", recipe.arguments)

    def test_vendor_picking_presets_and_unsupported_formats_fail(self):
        for item in [
            definition(
                "MGF", filters=[{"kind": "peak_picking", "algorithm": "vendor"}]
            ),
            definition("mzXML"),
            definition("mzML", preset="sage"),
            definition("mzML", compression="numpress"),
            definition("MGF", compression="numpress"),
            definition("MS2", compression="numpress"),
            definition("mzML", filters=[{"kind": "threshold", "threshold_type": "count", "value": 1.5}]),
            definition(
                "MGF",
                filters=[
                    {"kind": "threshold", "threshold_type": "relative", "value": 0.1}
                ],
            ),
        ]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                compile_recipe(item)

    def test_native_policies_cannot_be_silently_ignored_by_default_backend(self):
        recipe = definition("mzML", stored_centroids=True)
        recipe["converter"] = "msconvert"
        with self.assertRaisesRegex(ValueError, "explicit msconvert-rs"):
            compile_recipe(recipe)

    def test_resource_limits_and_flags_are_typed(self):
        for item in [
            definition(max_peaks_per_spectrum=-1),
            definition(allow_experimental="yes"),
            definition(max_memory_mb=True),
            definition(arbitrary_arguments=["--exec"]),
        ]:
            with self.assertRaises(ValueError):
                compile_recipe(item)

    def test_native_compression_level_is_explicit_and_typed(self):
        for level in (0, 6, 9):
            recipe = compile_recipe(definition("mzML", compression_level=level))
            position = recipe.arguments.index("--compression-level")
            self.assertEqual(recipe.arguments[position + 1], str(level))
        for parameters in (
            {"compression_level": -1}, {"compression_level": 10},
            {"compression_level": True}, {"compression_level": "6"},
            {"compression_level": 0, "compression": "none"},
        ):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                compile_recipe(definition("mzML", **parameters))
        for output in ("MGF", "MS2"):
            with self.subTest(output=output), self.assertRaises(ValueError):
                compile_recipe(definition(output, compression_level=0))
        item = definition("mzML", compression_level=0)
        item["converter"] = "msconvert"
        with self.assertRaisesRegex(ValueError, "explicit msconvert-rs"):
            compile_recipe(item)
        with self.assertRaises(ValueError):
            compile_recipe({**definition("mzML"), "converter": "msconvert"}, {"compression_level": 0})

    def test_missing_binary_configuration_has_no_fallback(self):
        from unittest.mock import patch

        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaisesRegex(ValueError, "SPECTARR_MSCONVERT_RS_BINARY"),
        ):
            NativeMsconvertRunner()

    def test_preflight_version_mismatch_does_not_run_conversion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "fake-native"
            binary.write_text(
                '#!/usr/bin/env python3\nimport json\nprint(json.dumps({"schema_version":1,"version":"0.1.0","outputs":["MGF"]}))\n'
            )
            binary.chmod(0o700)
            source = root / "source.mgf"
            source.write_text("placeholder")
            output = root / "output"
            output.mkdir()
            recipe = compile_recipe({**definition(), "converter_version": "99.0.0"})
            with self.assertRaisesRegex(ValueError, "version does not match"):
                NativeMsconvertRunner(binary).run(source, output, recipe, "unused")
            self.assertEqual(list(output.iterdir()), [])

    def test_process_output_bounds_are_enforced_during_execution(self):
        import sys
        import time

        for stream, amount, expected in [
            ("stdout", 8 * 1024 * 1024, "Native report"),
            ("stderr", 1024 * 1024, "Native stderr line"),
        ]:
            code = f"import sys, time\nsys.{stream}.write('x' * {amount})\nsys.{stream}.flush()\ntime.sleep(30)\n"
            started = time.monotonic()
            with (
                self.subTest(stream=stream),
                self.assertRaisesRegex(RuntimeError, expected),
            ):
                NativeMsconvertRunner._execute([sys.executable, "-c", code], None, None)
            self.assertLess(time.monotonic() - started, 8)

    def test_cancellation_stops_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            script = Path(directory) / "wait.py"
            script.write_text("import time\ntime.sleep(30)\n")
            cancelled = threading.Event()
            timer = threading.Timer(0.2, cancelled.set)
            timer.start()
            try:
                import sys

                result = NativeMsconvertRunner._execute(
                    [sys.executable, str(script)], cancelled, None
                )
            finally:
                timer.cancel()
            self.assertTrue(result.cancelled)
            self.assertNotEqual(result.return_code, 0)


@unittest.skipUnless(
    os.getenv("SPECTARR_MSCONVERT_RS_TEST_BINARY"),
    "Set a local native test binary for real conversion integration",
)
class NativeExecutableIntegrationTests(unittest.TestCase):
    def test_required_metadata_failure_cannot_fall_back_or_publish(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "unknown-polarity.mgf"
            source.write_text("BEGIN IONS\nTITLE=scan=1\nPEPMASS=500\nRTINSECONDS=0\n100 20\nEND IONS\n")
            fallback = Mock()
            service = ConversionService(
                root / "scratch", (root,), runner=fallback,
                native_runner=NativeMsconvertRunner(Path(os.environ["SPECTARR_MSCONVERT_RS_TEST_BINARY"])),
            )
            result = service.convert_with_control(ConversionRequest(
                "policy-rejected", str(source), "native-test",
                recipe_definition=definition("mzML", loss_policy={"require": ["polarity"]}),
            ))
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.outputs)
            fallback.run.assert_not_called()

    def test_content_addressed_input_alias_real_output_and_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = root / "sources"
            sources.mkdir()
            source = sources / "content-addressed-object"
            content = b"BEGIN IONS\nTITLE=scan=1\nPEPMASS=500\nRTINSECONDS=0\n100 20\nEND IONS\n"
            source.write_bytes(content)
            binary = Path(os.environ["SPECTARR_MSCONVERT_RS_TEST_BINARY"])
            fallback = Mock()
            service = ConversionService(
                root / "scratch",
                (sources,),
                runner=fallback,
                native_runner=NativeMsconvertRunner(binary),
            )
            phases = []
            result = service.convert_with_control(
                ConversionRequest(
                    "native-job",
                    str(source),
                    "native-test",
                    source_name="sample.mgf",
                    recipe_definition=definition("mzML", require_retention_time=True, compression_level=0, loss_policy={"deny": ["precision-reduction"], "require": []}),
                ),
                progress=lambda stage, value: phases.append(stage),
            )
            self.assertEqual(result.status, "succeeded", result.error)
            self.assertEqual(result.converter_backend, "msconvert-rs")
            self.assertTrue(result.image.startswith("msconvert-rs@sha256:"))
            self.assertEqual(
                result.converter_report["conversion"]["spectra_written"], 1
            )
            self.assertEqual(result.converter_report["source_name"], "sample.mgf")
            self.assertEqual(result.converter_report["preflight"]["status"], "planned")
            self.assertEqual(result.converter_report["preflight"]["recipe"]["options"]["compression_level"], 0)
            self.assertEqual(result.converter_report["conversion"]["options"]["compression_level"], 0)
            policy = {"deny": ["precision-reduction"], "require": []}
            self.assertEqual(result.converter_report["preflight"]["recipe"]["options"]["loss_policy"], policy)
            self.assertEqual(result.converter_report["conversion"]["options"]["loss_policy"], policy)
            self.assertTrue(Path(result.outputs[0].path).name.endswith(".mzML.gz"))
            self.assertEqual(source.read_bytes(), content)
            self.assertIn("writing", phases)
            fallback.run.assert_not_called()

    def test_real_native_cancellation_removes_worker_and_scratch(self):
        import subprocess
        import time
        from unittest.mock import patch

        processes = []
        workers = set()
        original_popen = subprocess.Popen

        def capture(*args, **kwargs):
            process = original_popen(*args, **kwargs)
            processes.append(process)
            return process

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.mgf"
            with source.open("w") as stream:
                for index in range(20000):
                    stream.write(
                        f"BEGIN IONS\nTITLE=scan={index + 1}\nPEPMASS=500\nRTINSECONDS=1\n100 20\nEND IONS\n"
                    )
            cancelled = threading.Event()
            stages = []

            def progress(stage, value):
                stages.append(stage)
                if stage == "hashing":
                    for process in processes:
                        tasks = Path(f"/proc/{process.pid}/task")
                        for children in tasks.glob("*/children"):
                            try:
                                workers.update(
                                    int(pid) for pid in children.read_text().split()
                                )
                            except FileNotFoundError:
                                pass
                    cancelled.set()

            service = ConversionService(
                root / "scratch",
                (root,),
                native_runner=NativeMsconvertRunner(
                    Path(os.environ["SPECTARR_MSCONVERT_RS_TEST_BINARY"])
                ),
            )
            started = time.monotonic()
            with patch(
                "spectarr_converter.native.subprocess.Popen", side_effect=capture
            ):
                result = service.convert_with_control(
                    ConversionRequest(
                        "native-cancel",
                        str(source),
                        "native-test",
                        recipe_definition=definition("mzML"),
                    ),
                    cancelled,
                    progress,
                )
            self.assertEqual(result.status, "cancelled", result.error)
            self.assertIn("hashing", stages)
            self.assertLess(time.monotonic() - started, 10)
            self.assertFalse((root / "scratch" / "native-cancel").exists())
            self.assertTrue(all(process.poll() is not None for process in processes))
            if Path("/proc").exists():
                self.assertTrue(
                    workers, "The hashing event must come from a real isolated worker"
                )
                self.assertTrue(
                    all(not Path(f"/proc/{pid}").exists() for pid in workers),
                    f"Residual native workers: {workers}",
                )

    def test_worker_uses_snapshot_and_records_native_provenance(self):
        from spectarr_converter.worker import ApiConversionWorker
        from test_worker import FakeApi

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "stored-object"
            source.write_text(
                "BEGIN IONS\nTITLE=scan=1\nPEPMASS=500\nRTINSECONDS=1\n100 20\nEND IONS\n"
            )
            api = FakeApi(source)
            original_post, original_get = api.post, api.get
            snapshot = {**definition("MGF"), "revision": 2}

            def post(path, payload=None):
                result = original_post(path, payload)
                if path.endswith("/claim"):
                    result["parameters"]["recipe_snapshot"] = snapshot
                return result

            def get(path, query=None):
                result = original_get(path, query)
                if path.endswith("/location"):
                    result["filename"] = "sample.mgf"
                return result

            api.post, api.get = post, get
            service = ConversionService(
                root / "scratch",
                (root,),
                native_runner=NativeMsconvertRunner(
                    Path(os.environ["SPECTARR_MSCONVERT_RS_TEST_BINARY"])
                ),
            )
            self.assertTrue(ApiConversionWorker(api, service).process_one())
            self.assertEqual(api.patches[-1]["state"], "succeeded", api.patches[-1])
            metadata = json.loads(api.upload[2]["metadata_json"])
            self.assertEqual(metadata["converter_backend"], "msconvert-rs")
            self.assertEqual(metadata["recipe_snapshot"], snapshot)
            self.assertEqual(
                metadata["converter_report"]["conversion"]["spectra_written"], 1
            )
            self.assertEqual(len(metadata["converter_report"]["binary_sha256"]), 64)
            self.assertEqual(metadata["converter_report"]["preflight"]["status"], "planned")

    def test_unknown_native_filter_fails_without_default_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.raw"
            source.write_bytes(b"RAW")
            fallback = Mock()
            service = ConversionService(
                root / "scratch",
                (root,),
                runner=fallback,
                native_runner=NativeMsconvertRunner(
                    Path(os.environ["SPECTARR_MSCONVERT_RS_TEST_BINARY"])
                ),
            )
            result = service.convert(
                ConversionRequest(
                    "native-error",
                    str(source),
                    "native-test",
                    recipe_definition=definition(
                        "MGF", filters=[{"kind": "peak_picking", "algorithm": "vendor"}]
                    ),
                )
            )
            self.assertEqual(result.status, "failed")
            fallback.run.assert_not_called()
