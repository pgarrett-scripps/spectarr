from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spectarr_extractor.spectra import SpectrumAccessError, SpxtacularSpectrumSource, _spxtacular_reader
from spectarr_extractor.spectrum_server import process_spectrum_request


def payload(scan: int) -> dict:
    return {
        "schema": "spxtacular.spectrum",
        "schema_version": 1,
        "kind": "msn_spectrum",
        "arrays": {
            "mz": [100.0],
            "intensity": [float(scan)],
            "charge": None,
            "im": None,
            "iso_score": None,
        },
        "metadata": {"scan_number": scan, "native_id": f"scan={scan}"},
    }


class FakeSpectrum:
    def __init__(self, scan: int) -> None:
        self.scan_number = scan
        self.native_id = f"scan={scan}"

    def to_dict(self) -> dict:
        return payload(self.scan_number)


class FakeLookup:
    def __init__(self, spectra: list[FakeSpectrum]) -> None:
        self.spectra = spectra
        self.lookups: list[int | str] = []

    def __iter__(self):
        return iter(self.spectra)

    def __getitem__(self, key: int | str) -> FakeSpectrum:
        self.lookups.append(key)
        if isinstance(key, int):
            return self.spectra[key]
        for spectrum in self.spectra:
            if spectrum.native_id == key:
                return spectrum
        raise KeyError(key)


class FakeReader:
    def __init__(self, _path: Path) -> None:
        self.ms1 = FakeLookup([FakeSpectrum(1), FakeSpectrum(3)])
        self.ms2 = FakeLookup([FakeSpectrum(2), FakeSpectrum(4)])

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None


class SpectrumSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "sample.mgf").write_text("fixture")
        self.source = SpxtacularSpectrumSource(self.root, FakeReader)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_selects_by_level_and_zero_based_position(self) -> None:
        result = self.source.read("sample.mgf", ms_level=2, index=1)
        self.assertEqual(result["metadata"]["scan_number"], 4)

    def test_selects_by_scan_number_or_native_id(self) -> None:
        by_scan = self.source.read("sample.mgf", ms_level=1, scan_number=3)
        by_native_id = self.source.read("sample.mgf", ms_level=2, native_id="scan=2")
        self.assertEqual(by_scan["metadata"]["scan_number"], 3)
        self.assertEqual(by_native_id["metadata"]["scan_number"], 2)

    def test_native_id_uses_random_access_lookup(self) -> None:
        reader = FakeReader(self.root / "sample.mgf")
        source = SpxtacularSpectrumSource(self.root, lambda _path: reader)

        result = source.read("sample.mgf", ms_level=2, native_id="scan=4")

        self.assertEqual(result["metadata"]["scan_number"], 4)
        self.assertEqual(reader.ms2.lookups, ["scan=4"])

    def test_rejects_path_escape(self) -> None:
        with self.assertRaisesRegex(SpectrumAccessError, "escapes") as raised:
            self.source.read("../outside.mgf", ms_level=2, index=0)
        self.assertEqual(raised.exception.status, 403)

    def test_reports_missing_spectrum(self) -> None:
        with self.assertRaisesRegex(
            SpectrumAccessError, "No matching spectrum"
        ) as raised:
            self.source.read("sample.mgf", ms_level=2, index=5)
        self.assertEqual(raised.exception.status, 404)

    def test_requires_exactly_one_selector(self) -> None:
        with self.assertRaisesRegex(SpectrumAccessError, "Exactly one"):
            self.source.read("sample.mgf", ms_level=2)
        with self.assertRaisesRegex(SpectrumAccessError, "Exactly one"):
                self.source.read("sample.mgf", ms_level=2, index=0, scan_number=2)

    def test_default_spxtacular_reader_requests_auto_disk_backed_mzml(self) -> None:
        calls = []

        def reader(path: Path, **kwargs: object) -> object:
            calls.append((path, kwargs))
            return object()

        with patch.dict("sys.modules", {"spxtacular": SimpleNamespace(Reader=reader)}):
            result = _spxtacular_reader(self.root / "sample.mgf")

        self.assertIsNotNone(result)
        self.assertEqual(
            calls,
            [
                (
                    self.root / "sample.mgf",
                    {"mzml_gzip_mode": "auto", "mzml_in_memory": False},
                )
            ],
        )


class FakeSource:
    def read(self, relative_path: str, **selection: object) -> dict:
        if relative_path != "library/sample.mgf":
            raise AssertionError(relative_path)
        if selection != {
            "ms_level": 2,
            "index": 0,
            "scan_number": None,
            "native_id": None,
        }:
            raise AssertionError(selection)
        return payload(2)


class SpectrumRequestTests(unittest.TestCase):
    def test_requires_worker_authentication(self) -> None:
        with self.assertRaisesRegex(
            SpectrumAccessError, "Valid worker token"
        ) as raised:
            process_spectrum_request(FakeSource(), "test-worker-token", "wrong", {})  # type: ignore[arg-type]
        self.assertEqual(raised.exception.status, 401)

    def test_returns_spxtacular_payload(self) -> None:
        result = process_spectrum_request(
            FakeSource(),  # type: ignore[arg-type]
            "test-worker-token",
            "test-worker-token",
            {
                "relative_path": "library/sample.mgf",
                "ms_level": 2,
                "index": 0,
                "scan_number": None,
                "native_id": None,
            },
        )
        self.assertEqual(result["schema"], "spxtacular.spectrum")
        self.assertEqual(result["metadata"]["scan_number"], 2)


if __name__ == "__main__":
    unittest.main()
