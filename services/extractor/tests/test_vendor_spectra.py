from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spectarr_extractor.spectra import SpectrumAccessError, SpxtacularSpectrumSource


class Record:
    """Mirror the provider's array ownership transfer and catalog identities."""

    def __init__(self, level):
        self.ms_level = level
        self.native_id = 'frame=1 scan=1' if level == 1 else 'frame=2 scan=150-175'
        self.scan_number = level
        self.retention_time_sec = 2400.0 + level
        self.scan_mode = 'centroid'
        self.polarity = 'positive'
        self.total_ion_current = 30.0
        self.inv_mobility = 1.1
        self.precursor = SimpleNamespace(mz=500.2, charge=2, isolation_target_mz=500.2,
                                         isolation_width=2.0, collision_energy=25.0) if level == 2 else None
        self.arrays = {'mz': [100.0, 200.0], 'intensity': [10.0, 20.0],
                       'inv_mobility_per_peak': [1.0, 1.2]}

    @property
    def mz(self):
        return self.arrays.pop('mz', [])

    @property
    def intensity(self):
        return self.arrays.pop('intensity', [])

    @property
    def inv_mobility_per_peak(self):
        return self.arrays.pop('inv_mobility_per_peak', [])


class VendorSpectrumTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'sample.d').mkdir()
        (self.root / 'sample.d/analysis.tdf').touch()
        (self.root / 'sample.raw').touch()
        available = patch('spectarr_extractor.providers.openmassspec.OpenMassSpecProvider.is_available', return_value=True)
        available.start()
        self.addCleanup(available.stop)
        self.closed = 0
        def spectra(_path):
            try:
                for level in (1, 2):
                    record = Record(level)
                    if str(_path).endswith('.raw'):
                        record.native_id = f'controllerType=0 controllerNumber=1 scan={level}'
                        record.scan_mode = 'profile'
                    yield record
            finally:
                self.closed += 1
        provider = SimpleNamespace(iter_spectra=spectra)
        mocked = patch('spectarr_extractor.vendor_spectra.OpenMassSpecProvider._module', return_value=provider)
        mocked.start()
        self.addCleanup(mocked.stop)
        self.source = SpxtacularSpectrumSource(self.root, prewarm_catalogs=False)

    def test_catalog_native_ids_retrieve_matching_ms1_and_ms2_arrays(self):
        for level in (1, 2):
            with self.subTest(level=level):
                native = Record(level).native_id
                result = self.source.read('sample.d', ms_level=level, native_id=native)
                self.assertEqual(result['metadata']['native_id'], native)
                self.assertEqual(result['metadata']['ms_level'], level)
                self.assertEqual(result['metadata']['rt'], 2400.0 + level)
                self.assertEqual(result['arrays']['mz'], [100.0, 200.0])
                self.assertEqual(result['arrays']['intensity'], [10.0, 20.0])
                self.assertEqual(result['arrays']['im'], [1.0, 1.2])
        self.assertEqual(self.closed, 2)
        self.assertEqual(result['metadata']['precursors'][0]['precursor_mz'], 500.2)
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['metadata']['precursors'][0]['im_type'], 'ook0')
        self.assertEqual(result['metadata']['precursors'][0]['intensity'], 0.0)
        self.assertEqual(result['metadata']['isolation_mz_range'], [499.2, 501.2])

    def test_raw_preserves_profile_representation_and_catalog_identity(self):
        native = 'controllerType=0 controllerNumber=1 scan=2'
        result = self.source.read('sample.raw', ms_level=2, native_id=native)
        self.assertEqual(result['metadata']['native_id'], native)
        self.assertEqual(result['metadata']['spectrum_type'], 'profile')
        self.assertEqual(result['arrays']['mz'], [100.0, 200.0])

    def test_unknown_or_wrong_level_identity_does_not_substitute_another_spectrum(self):
        for native in ('missing', 'frame=2 scan=150-175'):
            with self.subTest(native=native), self.assertRaises(SpectrumAccessError) as raised:
                self.source.read('sample.d', ms_level=1, native_id=native)
            self.assertEqual(raised.exception.status, 404)

    def test_browse_and_read_share_identity_and_peak_counts(self):
        page = self.source.browse('sample.d', ms_level=2)
        entry = page['items'][0]
        result = self.source.read('sample.d', ms_level=2, native_id=entry['native_id'])
        self.assertEqual(entry['peak_count'], len(result['arrays']['mz']))
        self.assertEqual(entry['scan_number'], result['metadata']['scan_number'])


if __name__ == '__main__':
    unittest.main()
