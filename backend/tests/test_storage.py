from __future__ import annotations

import hashlib
import stat
from io import BytesIO
from pathlib import Path

import pytest
from spectarr.library import original_extension, render_template, safe_component
from spectarr.storage import LocalArtifactStorage


class BrokenStream(BytesIO):
    def read(self, size: int = -1) -> bytes:
        if self.tell() > 0:
            raise OSError("source disappeared")
        return super().read(4)


def test_stream_deduplication(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path)
    first = storage.ingest_stream(BytesIO(b"same content"))
    second = storage.ingest_stream(BytesIO(b"same content"))
    assert first == second
    assert storage.resolve(first.key).read_bytes() == b"same content"
    assert first.sha256 == hashlib.sha256(b"same content").hexdigest()


def test_failed_stream_ingest_removes_staging_file(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path)
    with pytest.raises(OSError, match="source disappeared"):
        storage.ingest_stream(BrokenStream(b"incomplete payload"))
    assert list(storage.staging.iterdir()) == []


def test_bundle_identity_preserves_source_directory_name(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path / "storage")
    first = tmp_path / "one.d"
    second = tmp_path / "two.d"
    first.mkdir()
    second.mkdir()
    (first / "data.bin").write_bytes(b"data")
    (second / "data.bin").write_bytes(b"data")
    assert storage.ingest_path(first).sha256 != storage.ingest_path(second).sha256


def test_bundle_rejects_symbolic_links(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path / "storage")
    bundle = tmp_path / "linked.d"
    bundle.mkdir()
    target = tmp_path / "outside"
    target.write_bytes(b"secret")
    (bundle / "link").symlink_to(target)
    with pytest.raises(ValueError, match="symbolic links"):
        storage.ingest_path(bundle)


def test_materialized_file_keeps_original_name_and_uses_hardlink(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path / "storage")
    stored = storage.ingest_stream(BytesIO(b"raw bytes"))
    key = "project__1234/experiment__1234/run__1234/source/Sample.raw"
    mode = storage.materialize(stored.key, key, "Sample.raw")
    internal = storage.resolve(stored.key)
    readable = storage.resolve_library(key)
    assert mode == "hardlink"
    assert readable.read_bytes() == b"raw bytes"
    assert readable.name == "Sample.raw"
    assert internal.stat().st_ino == readable.stat().st_ino


def test_materialized_bundle_preserves_vendor_directory(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path / "storage")
    bundle = tmp_path / "Acquisition.d"
    (bundle / "AcqData").mkdir(parents=True)
    (bundle / "analysis.baf").write_bytes(b"binary")
    (bundle / "AcqData" / "method.xml").write_text("<method/>")
    stored = storage.ingest_path(bundle)
    key = "project/run/source/Acquisition.d"
    storage.materialize(stored.key, key, bundle.name)
    readable = storage.resolve_library(key)
    assert (readable / "analysis.baf").read_bytes() == b"binary"
    assert (readable / "AcqData" / "method.xml").read_text() == "<method/>"


def test_materialization_replaces_conflicting_destination_type(tmp_path: Path) -> None:
    storage = LocalArtifactStorage(tmp_path / "storage")
    file_object = storage.ingest_stream(BytesIO(b"file"))
    bundle = tmp_path / "Acquisition.d"
    bundle.mkdir()
    (bundle / "data.bin").write_bytes(b"bundle")
    bundle_object = storage.ingest_path(bundle)
    key = "project/run/source/acquisition"

    storage.materialize(bundle_object.key, key, bundle.name)
    assert storage.resolve_library(key).is_dir()
    storage.materialize(file_object.key, key, "source.raw")
    assert storage.resolve_library(key).read_bytes() == b"file"
    immutable_bundle_file = (
        storage.resolve(bundle_object.key) / "payload" / bundle.name / "data.bin"
    )
    assert stat.S_IMODE(immutable_bundle_file.stat().st_mode) == 0o444
    storage.materialize(bundle_object.key, key, bundle.name)
    assert (storage.resolve_library(key) / "data.bin").read_bytes() == b"bundle"


def test_library_naming_tokens_support_truncation_and_compound_extensions() -> None:
    rendered = render_template(
        "{run_name}__{run_id:8}{extension}",
        {"run_name": "sample-one", "run_id": "1234567890", "extension": ".mzML.gz"},
    )
    assert rendered == "sample-one__12345678.mzML.gz"
    assert original_extension("sample.mzML.gz") == ".mzML.gz"
    assert original_extension("sample.raw") == ".raw"
    assert safe_component("Project Name__12345678", slug=True) == "project-name__12345678"


def test_library_naming_rejects_unknown_tokens() -> None:
    with pytest.raises(ValueError, match="Unknown library naming token"):
        render_template("{not_a_token}", {})
