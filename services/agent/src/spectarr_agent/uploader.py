"""Resumable, idempotent upload execution."""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO, Callable, Iterator

from .api import ApiError, SpectarrAgentApi
from .discovery import AcquisitionChanged, AcquisitionScanner, Candidate
from .state import AgentState, QueueItem


class SourceUnavailable(RuntimeError):
    """The queued acquisition can no longer be read safely."""


class ResumableUploader:
    """Upload queue items while treating local acquisition data as read-only."""

    def __init__(
        self,
        api: SpectarrAgentApi,
        state: AgentState,
        scanner: AcquisitionScanner,
        agent_token: str,
        chunk_size: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api = api
        self.state = state
        self.scanner = scanner
        self.agent_token = agent_token
        self.chunk_size = chunk_size
        self.sleep = sleep

    def upload(self, item: QueueItem) -> tuple[str | None, bool]:
        self._verify_source_signature(item)
        status = self._session(item)
        if str(status.get("state")) == "completed":
            return self._artifact_id(status), True
        upload_id = str(status["id"])
        if item.source_kind == "bundle":
            self._upload_bundle(item, upload_id, status)
        else:
            self._upload_file(item.source_path, upload_id, int(status.get("offset", 0)))
        self._verify_source_signature(item)
        artifact = self.api.complete_upload(self.agent_token, upload_id)
        return self._artifact_id(artifact), False

    def _session(self, item: QueueItem) -> dict:
        if item.upload_id:
            return self.api.get_upload(self.agent_token, item.upload_id)
        metadata = {
            "agent_queue_id": item.id,
            "source_kind": item.source_kind,
        }
        bundle_manifest = None
        if item.manifest is not None:
            bundle_manifest = {
                "root_name": item.manifest["root_name"],
                "files": item.manifest["files"],
            }
        response = self.api.create_upload(
            self.agent_token,
            f"agent-upload:{item.checksum}",
            run_id=item.run_id,
            run=item.run,
            filename=item.source_name,
            format_name=item.format,
            total_size=item.byte_size if item.source_kind == "file" else None,
            sha256=item.checksum if item.source_kind == "file" else None,
            bundle_manifest=bundle_manifest,
            metadata=metadata,
        )
        upload_id = str(response["id"])
        self.state.set_upload_id(item.id, upload_id)
        return response

    def _upload_file(self, path: Path, upload_id: str, offset: int) -> None:
        try:
            size = path.stat().st_size
            with open_readonly(path) as stream:
                if offset > size:
                    raise SourceUnavailable("Server offset exceeds the local file size")
                stream.seek(offset)
                while offset < size:
                    content = stream.read(min(self.chunk_size, size - offset))
                    if not content:
                        raise SourceUnavailable("Acquisition file ended before its recorded size")
                    offset = self._send_chunk(upload_id, None, offset, content)
                    stream.seek(offset)
        except (FileNotFoundError, PermissionError) as error:
            raise SourceUnavailable(str(error)) from error

    def _upload_bundle(self, item: QueueItem, upload_id: str, status: dict) -> None:
        if item.manifest is None:
            raise SourceUnavailable("Bundle queue item has no manifest")
        offsets = {
            str(value["path"]): int(value.get("offset", 0))
            for value in status.get("files", [])
        }
        for record in item.manifest.get("files", []):
            relative = str(record["path"])
            path = safe_bundle_file(item.source_path, relative)
            expected_size = int(record["size"])
            offset = offsets.get(relative, 0)
            if path.stat().st_size != expected_size:
                raise SourceUnavailable(f"Bundle file size changed: {relative}")
            with open_readonly(path) as stream:
                if offset > expected_size:
                    raise SourceUnavailable(f"Server offset exceeds bundle file size: {relative}")
                stream.seek(offset)
                while offset < expected_size:
                    content = stream.read(min(self.chunk_size, expected_size - offset))
                    if not content:
                        raise SourceUnavailable(f"Bundle file ended early: {relative}")
                    offset = self._send_chunk(upload_id, relative, offset, content)
                    stream.seek(offset)

    def _send_chunk(self, upload_id: str, relative_path: str | None, offset: int, content: bytes) -> int:
        transient_attempts = 0
        while True:
            try:
                if relative_path is None:
                    next_offset = self.api.upload_chunk(self.agent_token, upload_id, offset, content)
                else:
                    next_offset = self.api.upload_bundle_chunk(
                        self.agent_token, upload_id, relative_path, offset, content
                    )
                expected = offset + len(content)
                if next_offset <= offset or next_offset > expected:
                    raise ApiError(502, "Server returned an impossible upload offset")
                return next_offset
            except ApiError as error:
                if error.status == 409 and error.expected_offset is not None:
                    expected = error.expected_offset
                    if offset <= expected <= offset + len(content):
                        return expected
                transient_attempts += 1
                if not error.retryable or transient_attempts >= 4:
                    raise
                self.sleep(min(2 ** (transient_attempts - 1), 8))

    def _verify_source_signature(self, item: QueueItem) -> None:
        if item.source_path.is_symlink():
            raise SourceUnavailable("Acquisition path is a symbolic link")
        candidate = Candidate(item.source_path, item.source_kind, item.format)
        try:
            snapshot = self.scanner.snapshot(candidate)
        except OSError as error:
            raise SourceUnavailable(str(error)) from error
        if snapshot.blocked or snapshot.signature != item.signature:
            raise AcquisitionChanged("Acquisition changed after it entered the upload queue")

    @staticmethod
    def _artifact_id(response: dict) -> str | None:
        if response.get("artifact_id"):
            return str(response["artifact_id"])
        if response.get("id") and "run_id" in response and "sha256" in response:
            return str(response["id"])
        artifact = response.get("artifact")
        return str(artifact["id"]) if isinstance(artifact, dict) and artifact.get("id") else None


def safe_bundle_file(root: Path, relative: str) -> Path:
    candidate = root
    for part in Path(relative).parts:
        if part in {"", ".", ".."}:
            raise SourceUnavailable("Bundle manifest contains an unsafe path")
        candidate = candidate / part
        if candidate.is_symlink():
            raise SourceUnavailable("Bundle path contains a symbolic link")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise SourceUnavailable("Bundle file escaped the acquisition directory")
    return resolved


@contextmanager
def open_readonly(path: Path) -> Iterator[BinaryIO]:
    if path.is_symlink():
        raise SourceUnavailable("Acquisition path is a symbolic link")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise SourceUnavailable(str(error)) from error
    with os.fdopen(descriptor, "rb") as stream:
        yield stream
