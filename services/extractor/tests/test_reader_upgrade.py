import base64
import gzip
import struct
import tempfile
import unittest
from pathlib import Path

from spxtacular import write_indexed_mzml_gzip
from spectarr_extractor.spectra import SpxtacularSpectrumSource, _spxtacular_reader


def mzml_fixture():
    arrays = []
    for accession, values in [("MS:1000514", [100.0, 200.0]), ("MS:1000515", [10.0, 20.0])]:
        encoded = base64.b64encode(struct.pack("<2d", *values)).decode()
        arrays.append(f'<binaryDataArray encodedLength="{len(encoded)}"><cvParam accession="MS:1000523"/><cvParam accession="MS:1000576"/><cvParam accession="{accession}"/><binary>{encoded}</binary></binaryDataArray>')
    return ('<?xml version="1.0"?><mzML xmlns="http://psi.hupo.org/ms/mzml" version="1.1.0"><run id="test"><spectrumList count="1">'
            '<spectrum id="controllerType=0 controllerNumber=1 scan=42" index="0" defaultArrayLength="2">'
            '<cvParam accession="MS:1000511" value="2"/><cvParam accession="MS:1000127"/>'
            '<scanList count="1"><scan><cvParam accession="MS:1000016" value="1.5" unitAccession="UO:0000031"/></scan></scanList>'
            '<precursorList count="1"><precursor><selectedIonList count="1"><selectedIon><cvParam accession="MS:1000744" value="500.25"/><cvParam accession="MS:1000041" value="2"/></selectedIon></selectedIonList></precursor></precursorList>'
            '<binaryDataArrayList count="2">' + ''.join(arrays) + '</binaryDataArrayList></spectrum></spectrumList></run></mzML>')


class ReaderUpgradeTests(unittest.TestCase):
    def test_compressed_access_preserves_metadata_without_extraction_or_source_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plain = root / 'test.mzML'
            plain.write_text(mzml_fixture())
            compressed = root / 'ordinary.mzML.gz'
            compressed.write_bytes(gzip.compress(plain.read_bytes()))
            indexed = root / 'indexed.mzML.gz'
            write_indexed_mzml_gzip(plain, indexed)
            before = {p.name: p.read_bytes() for p in root.iterdir()}
            for path, strategy in [(plain, 'plain'), (compressed, 'rapidgzip'), (indexed, 'embedded')]:
                with self.subTest(strategy=strategy):
                    with _spxtacular_reader(path) as reader:
                        self.assertEqual(reader.access_strategy, strategy)
                    source = SpxtacularSpectrumSource(root, prewarm_catalogs=False)
                    payload = source.read(path.name, ms_level=2, native_id='controllerType=0 controllerNumber=1 scan=42')
                    self.assertEqual(payload['schema_version'], 2)
                    self.assertEqual(payload['arrays']['mz'], [100.0, 200.0])
                    self.assertEqual(payload['arrays']['intensity'], [10.0, 20.0])
                    metadata = payload['metadata']
                    self.assertEqual(metadata['scan_number'], 42)
                    self.assertEqual(metadata['rt'], 90.0)
                    self.assertEqual(metadata['precursors'][0]['precursor_mz'], 500.25)
                    self.assertEqual(metadata['precursors'][0]['intensity'], 0.0)
                    summary = source.browse(path.name, ms_level=2)['items'][0]
                    self.assertEqual(summary['precursor_mz'], 500.25)
            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
