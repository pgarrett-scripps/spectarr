"""Acquisition polling and upload orchestration."""

from __future__ import annotations

import logging
import socket
import time
from pathlib import Path
from typing import Callable

from .api import ApiError, SpectarrAgentApi
from .config import AgentConfig
from .discovery import AcquisitionChanged, AcquisitionScanner, Candidate
from .state import AgentState, QueueItem
from .uploader import ResumableUploader, SourceUnavailable


LOGGER = logging.getLogger(__name__)


class AcquisitionAgent:
    """Keep discovery available offline and drain uploads when the API returns."""

    def __init__(
        self,
        config: AgentConfig,
        state: AgentState,
        api: SpectarrAgentApi,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.state = state
        self.api = api
        self.scanner = AcquisitionScanner(config)
        self.clock = clock
        self.sleep = sleep
        self._last_heartbeat = 0.0

    def scan_once(self) -> int:
        now = self.clock()
        queued = 0
        candidates = self.scanner.discover()
        for candidate in candidates:
            try:
                snapshot = self.scanner.snapshot(candidate)
            except OSError as error:
                LOGGER.warning("Cannot inspect %s: %s", candidate.path, error)
                continue
            stable = self.state.observe(
                candidate.path,
                snapshot.signature,
                now,
                self.config.stability_seconds,
                snapshot.reason if snapshot.blocked else None,
            )
            if snapshot.blocked:
                LOGGER.debug("Waiting for %s: %s", candidate.path.name, snapshot.reason)
                continue
            if not stable or not self.state.needs_hashing(candidate.path, snapshot.signature):
                continue
            try:
                acquisition = self.scanner.hash_candidate(candidate)
            except (AcquisitionChanged, OSError) as error:
                LOGGER.info("Acquisition changed during verification, will retry: %s", error)
                continue
            run_id, run = self._run_target(candidate)
            if self.config.dry_run:
                LOGGER.info(
                    "Dry run verified %s (%s bytes, sha256:%s)",
                    candidate.path,
                    acquisition.byte_size,
                    acquisition.checksum,
                )
                continue
            if self.state.enqueue(acquisition, run_id=run_id, run=run, now=now):
                queued += 1
                LOGGER.info("Queued acquisition %s", candidate.path)
        self.state.remove_stale_observations(now - max(self.config.poll_interval_seconds * 3, 60))
        return queued

    def upload_one(self) -> bool:
        if self.config.dry_run:
            return False
        item = self.state.claim_next(self.clock(), self.config.max_attempts)
        if item is None:
            return False
        try:
            credentials = self._credentials()
            uploader = ResumableUploader(
                self.api,
                self.state,
                self.scanner,
                credentials[1],
                self.config.chunk_size_bytes,
                self.sleep,
            )
            artifact_id, deduplicated = uploader.upload(item)
            self.state.complete(item.id, artifact_id, deduplicated)
            LOGGER.info("Uploaded %s", item.source_name)
        except (ApiError, OSError, SourceUnavailable, AcquisitionChanged, ValueError) as error:
            expired_session = isinstance(error, ApiError) and error.status in {404, 410} and item.upload_id
            if expired_session:
                self.state.clear_upload_id(item.id)
            permanent = isinstance(error, ApiError) and not error.retryable and error.status not in {409}
            if expired_session:
                permanent = False
            permanent = permanent or isinstance(error, (SourceUnavailable, AcquisitionChanged))
            if self.config.max_attempts and item.attempts >= self.config.max_attempts:
                permanent = True
            delay = min(
                self.config.retry_base_seconds * (2 ** max(0, item.attempts - 1)),
                self.config.retry_max_seconds,
            )
            self.state.retry(item.id, str(error), delay, permanent)
            LOGGER.warning("Upload deferred for %s: %s", item.source_name, error)
        return True

    def heartbeat_if_due(self, force: bool = False) -> None:
        if self.config.dry_run:
            return
        now = self.clock()
        if not force and now - self._last_heartbeat < self.config.heartbeat_interval_seconds:
            return
        try:
            agent_id, token = self._credentials()
            counts = self.state.counts()
            status = "busy" if counts.get("uploading", 0) else "online"
            self.api.heartbeat(
                agent_id,
                token,
                status,
                {
                    "queue": counts,
                    "watch_paths": [str(path) for path in self.config.watch_paths],
                },
            )
            self._last_heartbeat = now
        except ApiError as error:
            LOGGER.warning("Heartbeat failed: %s", error)

    def run_once(self) -> None:
        self.scan_once()
        while self.upload_one():
            pass
        self.heartbeat_if_due(force=True)

    def run_forever(self) -> None:
        LOGGER.info("Watching %d acquisition path(s)", len(self.config.watch_paths))
        while True:
            started = time.monotonic()
            self.scan_once()
            self.heartbeat_if_due()
            self.upload_one()
            elapsed = time.monotonic() - started
            self.sleep(max(0.1, self.config.poll_interval_seconds - elapsed))

    def _credentials(self) -> tuple[str, str]:
        agent_id = self.state.metadata("agent_id")
        token = self.state.metadata("agent_token")
        if agent_id and token:
            return agent_id, token
        if self.config.agent_id and self.config.agent_token:
            self.state.set_metadata("agent_id", self.config.agent_id)
            self.state.set_metadata("agent_token", self.config.agent_token)
            return self.config.agent_id, self.config.agent_token
        if not self.config.api_key:
            raise ApiError(401, "Agent is not registered and no bootstrap API key is configured")
        registration = self.api.register(
            self.config.api_key,
            self.config.agent_name or socket.gethostname(),
            self.state.local_agent_id(),
        )
        self.state.set_metadata("agent_id", registration.id)
        self.state.set_metadata("agent_token", registration.token)
        return registration.id, registration.token

    def _run_target(self, candidate: Candidate) -> tuple[str | None, dict | None]:
        if self.config.run_id:
            return self.config.run_id, None
        source_class = "vendor" if candidate.kind == "bundle" or candidate.format in {
            "RAW", "WIFF", "WIFF2"
        } else "spectrum_list" if candidate.format in {"MGF", "MS2"} else "open"
        name = candidate.path.name
        if candidate.path.suffix:
            name = candidate.path.name[: -len(candidate.path.suffix)]
        return None, {
            "experiment_id": self.config.experiment_id,
            "name": name,
            "sample_id": self.config.sample_id,
            "instrument_id": self.config.instrument_id,
            "source_class": source_class,
            "metadata_json": {"acquisition_filename": candidate.path.name},
        }
