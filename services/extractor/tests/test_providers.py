from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path

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
            '<?xml version="1.0"?><mzML><run><spectrumList count="1">'
            '<spectrum defaultArrayLength="0"><cvParam accession="MS:1000511" value="1"/>'
            '</spectrum></spectrumList></run></mzML>'
        ).encode()
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

    def test_openmassspec_selects_vendor_directory_bundle(self) -> None:
        class AvailableOpenMassSpec(OpenMassSpecProvider):
            def is_available(self) -> bool:
                return True

        self.assertTrue(
            AvailableOpenMassSpec().supports(self.root / "sample.d", "vendor_directory")
        )


if __name__ == "__main__":
    unittest.main()
