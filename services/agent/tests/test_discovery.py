from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from spectarr_agent.config import AgentConfig
from spectarr_agent.discovery import AcquisitionChanged, AcquisitionScanner


class DiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.config = AgentConfig(
            "http://localhost:8000",
            (self.root,),
            self.root / "queue.db",
            experiment_id="experiment-1",
        ).validate()
        self.scanner = AcquisitionScanner(self.config)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_discovers_supported_files_and_atomic_vendor_bundles(self) -> None:
        (self.root / "sample.mzML").write_text("mzml")
        (self.root / "active.raw.partial").write_text("partial")
        bundle = self.root / "bruker.d"
        bundle.mkdir()
        (bundle / "analysis.baf").write_bytes(b"data")
        (bundle / "nested.mgf").write_text("BEGIN IONS\nEND IONS\n")
        candidates = self.scanner.discover()
        self.assertEqual([item.path.name for item in candidates], ["bruker.d", "sample.mzML"])
        self.assertEqual(candidates[0].kind, "bundle")

    def test_bundle_waits_while_temp_or_lock_marker_exists(self) -> None:
        bundle = self.root / "active.d"
        bundle.mkdir()
        (bundle / "data.bin").write_bytes(b"data")
        (bundle / "acquisition.lock").write_text("locked")
        snapshot = self.scanner.snapshot(self.scanner.discover()[0])
        self.assertTrue(snapshot.blocked)
        self.assertIn("Temporary marker", snapshot.reason or "")

    def test_file_waits_while_related_lock_marker_exists(self) -> None:
        source = self.root / "active.raw"
        source.write_bytes(b"data")
        (self.root / "active.raw.lock").write_text("locked")
        snapshot = self.scanner.snapshot(self.scanner.discover()[0])
        self.assertTrue(snapshot.blocked)
        self.assertIn("active.raw.lock", snapshot.reason or "")

    def test_bundle_rejects_symbolic_links(self) -> None:
        bundle = self.root / "linked.d"
        bundle.mkdir()
        target = self.root / "outside.bin"
        target.write_bytes(b"secret")
        (bundle / "link.bin").symlink_to(target)
        snapshot = self.scanner.snapshot(self.scanner.discover()[0])
        self.assertTrue(snapshot.blocked)
        with self.assertRaises(AcquisitionChanged):
            self.scanner.hash_candidate(self.scanner.discover()[0])

    def test_hashes_file_and_bundle_without_modifying_sources(self) -> None:
        source = self.root / "sample.mgf"
        source.write_bytes(b"BEGIN IONS\nEND IONS\n")
        before = source.stat()
        acquisition = self.scanner.hash_candidate(self.scanner.discover()[0])
        after = source.stat()
        self.assertEqual(acquisition.byte_size, source.stat().st_size)
        self.assertEqual(len(acquisition.checksum), 64)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

        source.unlink()
        bundle = self.root / "sample.d"
        bundle.mkdir()
        (bundle / "b.bin").write_bytes(b"b")
        (bundle / "a.bin").write_bytes(b"a")
        acquisition = self.scanner.hash_candidate(self.scanner.discover()[0])
        self.assertEqual([item["path"] for item in acquisition.manifest["files"]], ["a.bin", "b.bin"])


if __name__ == "__main__":
    unittest.main()
