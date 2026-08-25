from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from spectarr_agent.api import ApiError
from spectarr_agent.config import AgentConfig
from spectarr_agent.discovery import AcquisitionScanner
from spectarr_agent.state import AgentState
from spectarr_agent.uploader import ResumableUploader, SourceUnavailable


class FakeApi:
    def __init__(self) -> None:
        self.data = bytearray()
        self.bundle_data: dict[str, bytearray] = {}
        self.create_calls = 0
        self.chunk_calls: list[tuple[str | None, int, bytes]] = []
        self.completed = 0
        self.deduplicated = False
        self.conflict_once = False

    def create_upload(self, token, key, **payload):
        self.create_calls += 1
        if self.deduplicated:
            return {"id": "upload-1", "state": "completed", "artifact_id": "existing"}
        if payload.get("bundle_manifest"):
            files = [
                {"path": value["path"], "offset": len(self.bundle_data.get(value["path"], b""))}
                for value in payload["bundle_manifest"]["files"]
            ]
            return {"id": "upload-1", "state": "uploading", "files": files}
        return {"id": "upload-1", "state": "uploading", "offset": len(self.data)}

    def get_upload(self, token, upload_id):
        return {"id": upload_id, "state": "uploading", "offset": len(self.data)}

    def upload_chunk(self, token, upload_id, offset, content):
        self.chunk_calls.append((None, offset, content))
        if self.conflict_once:
            self.conflict_once = False
            self.data.extend(content)
            raise ApiError(409, "response was lost", {"upload-offset": str(len(self.data))})
        if offset != len(self.data):
            raise ApiError(409, "offset", {"upload-offset": str(len(self.data))})
        self.data.extend(content)
        return len(self.data)

    def upload_bundle_chunk(self, token, upload_id, relative, offset, content):
        self.chunk_calls.append((relative, offset, content))
        target = self.bundle_data.setdefault(relative, bytearray())
        if offset != len(target):
            raise ApiError(409, "offset", {"upload-offset": str(len(target))})
        target.extend(content)
        return len(target)

    def complete_upload(self, token, upload_id):
        self.completed += 1
        return {"id": "artifact-1", "run_id": "run-1", "sha256": "a" * 64}


class UploaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        config = AgentConfig(
            "http://localhost:8000",
            (self.root,),
            self.root / "queue.db",
            run_id="run-1",
            chunk_size_bytes=64 * 1024,
        ).validate()
        self.scanner = AcquisitionScanner(config)
        self.state = AgentState(config.state_db)

    def tearDown(self) -> None:
        self.state.close()
        self.temporary.cleanup()

    def queue(self, path: Path):
        candidate = self.scanner.discover()[0]
        acquisition = self.scanner.hash_candidate(candidate)
        self.state.observe(path, acquisition.signature, 0, 0)
        self.state.enqueue(acquisition, run_id="run-1", now=0)
        return self.state.claim_next(now=1)

    def test_uploads_file_in_chunks_and_completes(self) -> None:
        source = self.root / "sample.mzML"
        content = b"x" * 150_000
        source.write_bytes(content)
        item = self.queue(source)
        api = FakeApi()
        artifact_id, deduplicated = ResumableUploader(
            api, self.state, self.scanner, "agt_token", 64 * 1024, lambda _: None
        ).upload(item)
        self.assertEqual(bytes(api.data), content)
        self.assertEqual(artifact_id, "artifact-1")
        self.assertFalse(deduplicated)
        self.assertEqual(api.completed, 1)

    def test_resumes_from_server_offset(self) -> None:
        source = self.root / "sample.raw"
        content = b"abcdef" * 20_000
        source.write_bytes(content)
        item = self.queue(source)
        api = FakeApi()
        api.data.extend(content[:70_000])
        artifact_id, _ = ResumableUploader(
            api, self.state, self.scanner, "agt_token", 64 * 1024, lambda _: None
        ).upload(item)
        self.assertEqual(bytes(api.data), content)
        self.assertEqual(artifact_id, "artifact-1")

    def test_recovers_when_chunk_commit_response_is_lost(self) -> None:
        source = self.root / "sample.raw"
        content = b"abcdef" * 20_000
        source.write_bytes(content)
        item = self.queue(source)
        api = FakeApi()
        api.conflict_once = True
        ResumableUploader(
            api, self.state, self.scanner, "agt_token", 64 * 1024, lambda _: None
        ).upload(item)
        self.assertEqual(bytes(api.data), content)

    def test_server_side_checksum_dedup_skips_bytes(self) -> None:
        source = self.root / "sample.mgf"
        source.write_text("BEGIN IONS\nEND IONS\n")
        item = self.queue(source)
        api = FakeApi()
        api.deduplicated = True
        artifact_id, deduplicated = ResumableUploader(
            api, self.state, self.scanner, "agt_token", 64 * 1024, lambda _: None
        ).upload(item)
        self.assertEqual(artifact_id, "existing")
        self.assertTrue(deduplicated)
        self.assertEqual(api.chunk_calls, [])

    def test_uploads_native_bundle_members_without_archive(self) -> None:
        bundle = self.root / "sample.d"
        bundle.mkdir()
        files = {"a.bin": b"a" * 70_000, "nested/b.bin": b"b" * 10}
        for relative, content in files.items():
            path = bundle / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        item = self.queue(bundle)
        api = FakeApi()
        api.bundle_data["a.bin"] = bytearray(files["a.bin"][:1024])
        artifact_id, _ = ResumableUploader(
            api, self.state, self.scanner, "agt_token", 64 * 1024, lambda _: None
        ).upload(item)
        self.assertEqual(artifact_id, "artifact-1")
        self.assertEqual({key: bytes(value) for key, value in api.bundle_data.items()}, files)
        self.assertEqual(
            item.checksum,
            hashlib.sha256(
                __import__("json").dumps(item.manifest, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        )

    def test_rejects_source_replaced_by_symbolic_link_before_session_creation(self) -> None:
        source = self.root / "sample.raw"
        source.write_bytes(b"original")
        item = self.queue(source)
        target = self.root / "replacement.raw"
        target.write_bytes(b"replacement")
        source.unlink()
        source.symlink_to(target)
        api = FakeApi()
        with self.assertRaises(SourceUnavailable):
            ResumableUploader(
                api, self.state, self.scanner, "agt_token", 64 * 1024, lambda _: None
            ).upload(item)
        self.assertEqual(api.create_calls, 0)


if __name__ == "__main__":
    unittest.main()
