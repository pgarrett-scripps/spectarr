"""Configuration loading for the acquisition agent."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any


DEFAULT_IGNORE_PATTERNS = (
    "*.tmp",
    "*.temp",
    "*.partial",
    "*.part",
    "*.lock",
    "*.lck",
    "*.download",
    "*.crdownload",
    "*.inprogress",
    "~*",
    ".~lock.*",
)
DEFAULT_FILE_SUFFIXES = (
    ".raw",
    ".wiff",
    ".wiff2",
    ".mzml",
    ".mzxml",
    ".mgf",
    ".ms2",
    ".baf",
    ".tdf",
    ".tsf",
)
DEFAULT_BUNDLE_SUFFIXES = (".d", ".raw")


@dataclass(frozen=True)
class AgentConfig:
    """Validated runtime configuration."""

    server_url: str
    watch_paths: tuple[Path, ...]
    state_db: Path
    api_key: str | None = None
    agent_id: str | None = None
    agent_token: str | None = None
    agent_name: str | None = None
    run_id: str | None = None
    experiment_id: str | None = None
    sample_id: str | None = None
    instrument_id: str | None = None
    poll_interval_seconds: float = 10.0
    stability_seconds: float = 120.0
    heartbeat_interval_seconds: float = 30.0
    chunk_size_bytes: int = 8 * 1024 * 1024
    request_timeout_seconds: float = 60.0
    retry_base_seconds: float = 2.0
    retry_max_seconds: float = 300.0
    max_attempts: int = 0
    ignore_patterns: tuple[str, ...] = field(default_factory=lambda: DEFAULT_IGNORE_PATTERNS)
    file_suffixes: tuple[str, ...] = field(default_factory=lambda: DEFAULT_FILE_SUFFIXES)
    bundle_suffixes: tuple[str, ...] = field(default_factory=lambda: DEFAULT_BUNDLE_SUFFIXES)
    dry_run: bool = False

    def validate(self) -> "AgentConfig":
        if not self.server_url.startswith(("http://", "https://")):
            raise ValueError("server_url must use http or https")
        if not self.watch_paths:
            raise ValueError("At least one watch path is required")
        if bool(self.agent_id) != bool(self.agent_token):
            raise ValueError("agent_id and agent_token must be configured together")
        if self.agent_token and not self.agent_token.startswith("agt_"):
            raise ValueError("agent_token must be a Spectarr agent token")
        if self.run_id and self.experiment_id:
            raise ValueError("Configure at most one of run_id or experiment_id")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if self.stability_seconds < 0:
            raise ValueError("stability_seconds cannot be negative")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.chunk_size_bytes < 64 * 1024:
            raise ValueError("chunk_size_bytes must be at least 65536")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.retry_base_seconds <= 0 or self.retry_max_seconds < self.retry_base_seconds:
            raise ValueError("Retry delays are invalid")
        if self.max_attempts < 0:
            raise ValueError("max_attempts cannot be negative")
        return replace(
            self,
            server_url=self.server_url.rstrip("/"),
            watch_paths=tuple(path.expanduser().resolve(strict=False) for path in self.watch_paths),
            state_db=self.state_db.expanduser().resolve(strict=False),
            file_suffixes=tuple(value.lower() for value in self.file_suffixes),
            bundle_suffixes=tuple(value.lower() for value in self.bundle_suffixes),
        )


def load_config(path: Path | None = None, overrides: dict[str, Any] | None = None) -> AgentConfig:
    """Load TOML, environment values, and explicit CLI overrides in that order."""

    raw: dict[str, Any] = {}
    if path is not None:
        with path.expanduser().open("rb") as stream:
            document = tomllib.load(stream)
        raw.update(document.get("agent", document))

    environment: dict[str, Any] = {}
    env_map = {
        "server_url": "SPECTARR_URL",
        "api_key": "SPECTARR_API_KEY",
        "agent_id": "SPECTARR_AGENT_ID",
        "agent_token": "SPECTARR_AGENT_TOKEN",
        "agent_name": "SPECTARR_AGENT_NAME",
        "run_id": "SPECTARR_AGENT_RUN_ID",
        "experiment_id": "SPECTARR_AGENT_EXPERIMENT_ID",
        "sample_id": "SPECTARR_AGENT_SAMPLE_ID",
        "instrument_id": "SPECTARR_AGENT_INSTRUMENT_ID",
        "state_db": "SPECTARR_AGENT_STATE_DB",
        "poll_interval_seconds": "SPECTARR_AGENT_POLL_SECONDS",
        "stability_seconds": "SPECTARR_AGENT_STABILITY_SECONDS",
        "heartbeat_interval_seconds": "SPECTARR_AGENT_HEARTBEAT_SECONDS",
        "chunk_size_bytes": "SPECTARR_AGENT_CHUNK_SIZE",
        "request_timeout_seconds": "SPECTARR_AGENT_TIMEOUT_SECONDS",
        "retry_base_seconds": "SPECTARR_AGENT_RETRY_BASE_SECONDS",
        "retry_max_seconds": "SPECTARR_AGENT_RETRY_MAX_SECONDS",
        "max_attempts": "SPECTARR_AGENT_MAX_ATTEMPTS",
    }
    for key, variable in env_map.items():
        value = os.getenv(variable)
        if value not in {None, ""}:
            environment[key] = value
    watch_value = os.getenv("SPECTARR_AGENT_WATCH_PATHS")
    if watch_value:
        environment["watch_paths"] = [value for value in watch_value.split(os.pathsep) if value]
    raw.update(environment)
    raw.update({key: value for key, value in (overrides or {}).items() if value is not None})

    numeric_float = {
        "poll_interval_seconds",
        "stability_seconds",
        "heartbeat_interval_seconds",
        "request_timeout_seconds",
        "retry_base_seconds",
        "retry_max_seconds",
    }
    numeric_int = {"chunk_size_bytes", "max_attempts"}
    for key in numeric_float:
        if key in raw:
            raw[key] = float(raw[key])
    for key in numeric_int:
        if key in raw:
            raw[key] = int(raw[key])

    if "watch_paths" in raw:
        raw["watch_paths"] = tuple(Path(value) for value in raw["watch_paths"])
    if "state_db" in raw:
        raw["state_db"] = Path(raw["state_db"])
    else:
        raw["state_db"] = Path.home() / ".spectarr-agent" / "queue.sqlite3"
    for key in ("ignore_patterns", "file_suffixes", "bundle_suffixes"):
        if key in raw:
            raw[key] = tuple(str(value) for value in raw[key])
    if "server_url" not in raw:
        raw["server_url"] = "http://localhost:8000"
    return AgentConfig(**raw).validate()
