from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spectarr_extractor.models import BoundedSeries
from spectarr_extractor.providers import ProviderRegistry
from spectarr_extractor.providers.base import ProviderError
from spectarr_extractor.providers.mgf import MgfProvider
from spectarr_extractor.providers.ms2 import Ms2Provider
from spectarr_extractor.providers.openmassspec import OpenMassSpecProvider
from spectarr_extractor.providers.xml_formats import MzmlProvider, MzxmlProvider


class ProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_mgf_streaming_summary(self) -> None:
        path = self.root / "sample.mgf"
        path.write_text(
            "COM=demo\n"
            "BEGIN IONS\nTITLE=one\nPEPMASS=500.2 100\nCHARGE=2+\nRTINSECONDS=60\n"
            "100.0 10\n200.0 30\nEND IONS\n"
            "BEGIN IONS\nPEPMASS=600.2\nRTINSECONDS=120\n150.0 5\nEND IONS\n"
        )
        result = MgfProvider().extract(path)
        summary = result.qc_summary
        self.assertEqual(summary["spectrum_count"], 2)
        self.assertEqual(summary["spectra_by_ms_level"], {"2": 2})
        self.assertEqual(summary["precursors"]["charge_counts"], {"2": 1})
        self.assertEqual(summary["acquisition_duration_seconds"], 60.0)
        self.assertEqual(summary["tic"]["max"], 40.0)

    def test_ms2_streaming_summary(self) -> None:
        path = self.root / "sample.ms2"
        path.write_text(
            "H\tCreationDate\t2026-08-25\n"
            "S\t1\t1\t500.2\nI\tRetTime\t12.5\nZ\t2\t999.4\n100 5\n200 10\n"
            "S\t2\t2\t600.4\nI\tRetTime\t22.5\n150 7\n"
        )
        result = Ms2Provider().extract(path)
        self.assertEqual(result.qc_summary["spectrum_count"], 2)
        self.assertEqual(result.qc_summary["peak_count"]["max"], 2.0)
        self.assertEqual(result.qc_summary["mz_range"], {"min": 100.0, "max": 200.0})

    def test_gzip_peak_lists_and_content_addressed_objects(self) -> None:
        fixtures = [
            ("mgf", MgfProvider(), b"BEGIN IONS\nPEPMASS=500\n100 50\nEND IONS\n"),
            ("ms2", Ms2Provider(), b"S\t1\t1\t500\n100 50\n"),
        ]
        for extension, provider, content in fixtures:
            for filename in (f"sample.{extension}.gz", "content-addressed-object"):
                with self.subTest(filename=filename, format=extension):
                    path = self.root / filename
                    path.write_bytes(gzip.compress(content))
                    declared = extension.upper() if filename == "content-addressed-object" else None
                    self.assertTrue(provider.supports(path, declared))
                    result = provider.extract(path, declared)
                    self.assertEqual(result.qc_summary["spectrum_count"], 1)
                    self.assertEqual(result.qc_summary["peak_count"]["max"], 1)

    def test_mzml_cv_metadata(self) -> None:
        path = self.root / "sample.mzML"
        path.write_text(
            '<?xml version="1.0"?>\n'
            '<mzML xmlns="http://psi.hupo.org/ms/mzml"><run id="run-1"><spectrumList count="1">'
            '<spectrum id="scan=1" defaultArrayLength="20">'
            '<cvParam accession="MS:1000511" name="ms level" value="2"/>'
            '<cvParam accession="MS:1000130" name="positive scan" value=""/>'
            '<cvParam accession="MS:1000127" name="centroid spectrum" value=""/>'
            '<cvParam accession="MS:1000016" name="scan start time" value="2" unitName="minute"/>'
            '<cvParam accession="MS:1000528" name="lowest observed m/z" value="100"/>'
            '<cvParam accession="MS:1000527" name="highest observed m/z" value="1200"/>'
            '<cvParam accession="MS:1000285" name="total ion current" value="5000"/>'
            '<cvParam accession="MS:1000505" name="base peak intensity" value="800"/>'
            '<cvParam accession="MS:1000744" name="selected ion m/z" value="500.2"/>'
            '<cvParam accession="MS:1000041" name="charge state" value="2"/>'
            '<cvParam accession="MS:1000045" name="collision energy" value="30"/>'
            '<cvParam accession="MS:1000000" name="data independent acquisition" value=""/>'
            '<cvParam accession="MS:1000827" name="isolation window target m/z" value="500"/>'
            '<cvParam accession="MS:1000828" name="isolation window lower offset" value="10"/>'
            '<cvParam accession="MS:1000829" name="isolation window upper offset" value="10"/>'
            '</spectrum></spectrumList></run></mzML>'
        )
        result = MzmlProvider().extract(path)
        summary = result.qc_summary
        self.assertEqual(summary["spectrum_count"], 1)
        self.assertEqual(summary["retention_time_seconds"]["min"], 120.0)
        self.assertEqual(summary["precursors"]["collision_energy"]["mean"], 30.0)
        self.assertEqual(summary["dia"]["windows"][0]["lower_mz"], 490.0)
        self.assertTrue(summary["dia"]["detected"])

    def test_gzipped_mzml_is_detected_and_streamed(self) -> None:
        path = self.root / "sample.mzML.gz"
        with gzip.open(path, "wt") as stream:
            stream.write(
                '<?xml version="1.0"?><mzML><run><spectrumList count="1">'
                '<spectrum defaultArrayLength="0"><cvParam accession="MS:1000511" value="1"/>'
                '</spectrum></spectrumList></run></mzML>'
            )
        result = ProviderRegistry().extract(path)
        self.assertEqual(result.source_format, "mzML")
        self.assertEqual(result.qc_summary["spectrum_count"], 1)

    def test_content_addressed_gzip_is_detected_by_magic_bytes(self) -> None:
        path = self.root / "sha256-object-without-extension"
        document = (
            b'<?xml version="1.0"?><mzML><run><spectrumList count="1">'
            b'<spectrum defaultArrayLength="0"><cvParam accession="MS:1000511" value="1"/>'
            b'</spectrum></spectrumList></run></mzML>'
        )
        path.write_bytes(gzip.compress(document))
        result = ProviderRegistry().extract(path, "mzML")
        self.assertEqual(result.qc_summary["spectrum_count"], 1)

    def test_mzxml_metadata(self) -> None:
        path = self.root / "sample.mzXML"
        path.write_text(
            '<?xml version="1.0"?><mzXML><msRun scanCount="1">'
            '<scan num="1" msLevel="2" peaksCount="10" polarity="-" retentionTime="PT1M2.5S" '
            'lowMz="50" highMz="900" totIonCurrent="1200" basePeakIntensity="400">'
            '<precursorMz precursorCharge="3" windowWideness="20">450.5</precursorMz>'
            '</scan></msRun></mzXML>'
        )
        result = MzxmlProvider().extract(path)
        summary = result.qc_summary
        self.assertEqual(summary["retention_time_seconds"]["min"], 62.5)
        self.assertEqual(summary["polarities"], ["negative"])
        self.assertEqual(summary["precursors"]["charge_counts"], {"3": 1})

    def test_preview_is_bounded(self) -> None:
        series = BoundedSeries(maximum_points=10)
        for value in range(1000):
            series.add(float(value), float(value * 2))
        preview = series.finish()
        self.assertLessEqual(len(preview), 10)
        self.assertEqual(preview[-1]["retention_time_seconds"], 999.0)

    def test_optional_provider_failure_falls_back(self) -> None:
        class BrokenOptional:
            name = "optional"
            version = "1"
            optional = True

            def supports(self, path, declared_format=None):
                return True

            def extract(self, path, declared_format=None):
                raise ProviderError("broken")

        path = self.root / "sample.mgf"
        path.write_text("BEGIN IONS\n100 2\nEND IONS\n")
        result = ProviderRegistry([BrokenOptional(), MgfProvider()]).extract(path)
        self.assertEqual(result.parser_provider, "spectarr-mgf")
        self.assertIn("optional failed", result.warnings[0])

    def test_openmassspec_selects_declared_raw_case_insensitively(self) -> None:
        class AvailableOpenMassSpec(OpenMassSpecProvider):
            def is_available(self) -> bool:
                return True

        self.assertTrue(AvailableOpenMassSpec().supports(self.root / "content-hash", "RAW"))

    def test_openmassspec_preserves_nested_precursor_metadata(self) -> None:
        precursor = {"selected_mz": 352.515, "target_mz": 352.5, "charge": 3,
                     "isolation_width": 1.3, "collision_energy": 27.0, "activation": "hcd"}
        for nested in (precursor, SimpleNamespace(**precursor)):
            spectrum = SimpleNamespace(ms_level=2, mz=[100.0, 200.0], intensity=[10.0, 30.0],
                                       retention_time_sec=60.0, precursor=nested)
            module = SimpleNamespace(iter_spectra=lambda _: iter([spectrum]))
            observations = []
            with patch.object(OpenMassSpecProvider, "_module", return_value=module):
                OpenMassSpecProvider().extract(self.root / "sample.raw", on_spectrum=observations.append)
            value = observations[0]
            self.assertEqual(value.precursor_mz, 352.515)
            self.assertEqual(value.precursor_charge, 3)
            self.assertEqual(value.isolation_target_mz, 352.5)
            self.assertEqual(value.isolation_lower_offset, 0.65)
            self.assertEqual(value.isolation_upper_offset, 0.65)
            self.assertEqual(value.collision_energy, 27.0)
            self.assertEqual(value.activation_type, "hcd")

    def test_openmassspec_preserves_flat_precursors_and_missing_ms1_precursors(self) -> None:
        spectra = [SimpleNamespace(ms_level=1, mz=[], intensity=[]),
                   SimpleNamespace(ms_level=2, mz=[], intensity=[], precursor_mz=500.2,
                                   precursor_charge=2, precursor={"selected_mz": 999.0, "charge": 4})]
        observations = []
        module = SimpleNamespace(iter_spectra=lambda _: iter(spectra))
        with patch.object(OpenMassSpecProvider, "_module", return_value=module):
            OpenMassSpecProvider().extract(self.root / "sample.raw", on_spectrum=observations.append)
        self.assertIsNone(observations[0].precursor_mz)
        self.assertIsNone(observations[0].isolation_lower_offset)
        self.assertEqual(observations[1].precursor_mz, 500.2)
        self.assertEqual(observations[1].precursor_charge, 2)

    def test_openmassspec_selects_vendor_directory_bundle(self) -> None:
        class AvailableOpenMassSpec(OpenMassSpecProvider):
            def is_available(self) -> bool:
                return True

        self.assertTrue(
            AvailableOpenMassSpec().supports(self.root / "sample.d", "vendor_directory")
        )

    def test_openmassspec_leaves_open_formats_to_builtin_parsers(self) -> None:
        class AvailableOpenMassSpec(OpenMassSpecProvider):
            def is_available(self) -> bool:
                return True

        provider = AvailableOpenMassSpec()
        self.assertFalse(provider.supports(self.root / "sample.mzML", "mzML"))
        self.assertFalse(provider.supports(self.root / "sample.mgf", "MGF"))


if __name__ == "__main__":
    unittest.main()
