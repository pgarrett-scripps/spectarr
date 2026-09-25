import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from spectarr_agent.api import ApiError
from spectarr_agent.catalog import CatalogAgent
from spectarr_agent.config import AgentConfig
from spectarr_agent.discovery import AcquisitionScanner
from spectarr_agent.readonly import open_source, root_identity
from spectarr_agent.state import AgentState


class FakeApi:
    def __init__(self, root):
        self.task = {'id': 'scan-1', 'kind': 'scan', 'sequence': 0, 'root': {'path': str(root), 'identity': None}}
        self.reports = []
        self.results = []
        self.drop = False

    def _request(self, method, path, **kwargs):
        if method == 'GET':
            return {'items': [self.task]}, {}
        body = kwargs['json_body']
        if path.endswith('/observations'):
            self.reports.append(body)
            self.task['sequence'] += 1
            if self.drop:
                self.drop = False
                raise ApiError(0, 'lost response')
            return {'next_sequence': self.task['sequence']}, {}
        self.results.append(body)
        return {'state': body['status']}, {}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)
        self.root = self.path / 'archive'
        self.root.mkdir()
        self.config = AgentConfig('http://localhost', (self.root,), self.path / 'state.db', mode='catalog', stability_seconds=0).validate()
        self.state = AgentState(self.config.state_db)
        self.api = FakeApi(self.root)
        self.agent = CatalogAgent(self.config, self.state, self.api, 'token')

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_catalog_resumes_after_lost_response_without_uploading(self):
        source = self.root / 'one.mgf'
        source.write_bytes(b'original')
        source.chmod(0o444)
        self.root.chmod(0o555)
        self.api.drop = True
        with self.assertRaises(ApiError):
            self.agent.tick()
        self.agent.tick()
        self.assertEqual(len(self.api.reports), 1)
        self.assertEqual(self.api.results[-1]['status'], 'complete')
        self.assertEqual(source.read_bytes(), b'original')
        self.assertEqual(self.state.counts(), {})
        self.root.chmod(0o755)

    def test_missing_checkpoint_suppresses_missing_reconciliation(self):
        (self.root / 'one.mgf').touch()
        self.api.task['sequence'] = 4
        self.agent.tick()
        self.assertEqual(self.api.reports[0]['sequence'], 4)
        self.assertEqual(self.api.results[-1]['status'], 'partial')

    def test_offline_root_and_root_replacement_are_distinct(self):
        self.api.task['root']['identity'] = root_identity(self.root)
        self.root.rename(self.path / 'old')
        self.agent.tick()
        self.assertEqual(self.api.results[-1]['status'], 'unavailable')
        self.root.mkdir()
        self.agent.tick()
        self.assertEqual(self.api.results[-1]['status'], 'failed')
        self.assertEqual(self.api.reports, [])

    def test_allowlist_and_symlinked_ancestors_reject_reads(self):
        outside = self.path / 'elsewhere'
        outside.mkdir()
        (outside / 'one.mgf').write_text('private')
        self.api.task['root']['path'] = str(outside)
        self.agent.tick()
        self.assertEqual(self.api.results[-1]['status'], 'failed')
        link = self.root / 'link'
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            with open_source(link / 'one.mgf'):
                self.fail('Linked ancestors must not open')

    def test_partial_traversal_and_scan_limit_never_claim_completion(self):
        for name in ['a.mgf', 'b.mgf']:
            (self.root / name).write_text(name)
        self.agent.config = self.config.__class__(**{**self.config.__dict__, 'scan_max_entries': 1})
        self.agent.tick()
        self.assertEqual(self.api.results[-1]['status'], 'partial')
        self.assertEqual(len(self.api.reports[0]['files']), 1)

    def test_bundle_is_atomic_and_changed_bytes_are_reverified(self):
        bundle = self.root / 'one.d'
        bundle.mkdir()
        member = bundle / 'analysis.tdf'
        member.write_bytes(b'aaaa')
        self.agent.tick()
        self.assertEqual(len(self.api.reports[0]['files']), 1)
        fact = self.api.reports[0]['files'][0]
        self.api.task = {'id': 'verify', 'kind': 'verify', 'root': self.api.task['root'], 'entry': {'format': 'vendor_directory', 'kind': 'bundle'}, 'location': {'relative_path': 'one.d'}}
        self.agent.tick()
        before = self.api.results[-1]['sha256']
        info = member.stat()
        member.write_bytes(b'bbbb')
        os.utime(member, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.agent.tick()
        self.assertNotEqual(before, self.api.results[-1]['sha256'])
        self.assertEqual(fact['kind'], 'bundle')

    def test_published_marker_is_required_even_after_stability(self):
        source = self.root / 'one.mgf'
        source.write_bytes(b'data')
        config = self.config.__class__(**{**self.config.__dict__, 'completion_policy': 'published_marker'})
        scanner = AcquisitionScanner(config)
        candidate = scanner.discover()[0]
        self.assertFalse(scanner.published(candidate))
        marker = Path(str(source) + '.complete')
        marker.touch()
        self.assertTrue(scanner.published(candidate))

    def test_state_cannot_live_in_the_source_root(self):
        with self.assertRaises(ValueError):
            self.config.__class__(**{**self.config.__dict__, 'state_db': self.root / 'state.db'}).validate()

    def test_scan_errors_are_reported_by_shared_scanner(self):
        scanner = AcquisitionScanner(self.config)
        with patch.object(Path, 'iterdir', side_effect=PermissionError('denied')):
            self.assertEqual(scanner.discover(), [])
        self.assertIn('denied', scanner.errors[0])

    def test_mixed_case_bundle_hash_is_canonical(self):
        bundle = self.root / 'case.d'
        bundle.mkdir()
        (bundle / 'z.bin').write_bytes(b'z')
        (bundle / 'Z.bin').write_bytes(b'Z')
        scanner = AcquisitionScanner(self.config)
        item = scanner.hash_candidate(scanner.discover()[0])
        self.assertEqual([f['path'] for f in item.manifest['files']], ['Z.bin', 'z.bin'])
        self.assertEqual(json.loads(json.dumps(item.manifest))['byte_size'], 2)
