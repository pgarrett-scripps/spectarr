"""Webhook worker configuration."""

from __future__ import annotations

import os
import socket
from dataclasses import dataclass, replace


def environment_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class WorkerConfig:
    server_url: str = "http://api:8000"
    service_token: str | None = None
    worker_token: str | None = None
    worker_id: str = ""
    poll_seconds: float = 3.0
    request_timeout_seconds: float = 15.0
    batch_size: int = 25
    max_response_bytes: int = 64 * 1024
    allow_http_destinations: bool = False

    def validate(self) -> "WorkerConfig":
        if not self.server_url.startswith(("http://", "https://")):
            raise ValueError("SPECTARR_URL must use http or https")
        if bool(self.service_token) == bool(self.worker_token):
            raise ValueError("Configure exactly one service token or legacy worker token")
        if not self.worker_id.strip():
            raise ValueError("worker_id cannot be empty")
        if self.poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        if not 0 < self.request_timeout_seconds <= 120:
            raise ValueError("request_timeout_seconds must be between 0 and 120")
        if not 1 <= self.batch_size <= 100:
            raise ValueError("batch_size must be between 1 and 100")
        if not 1024 <= self.max_response_bytes <= 1024 * 1024:
            raise ValueError("max_response_bytes must be between 1024 and 1048576")
        return replace(
            self,
            server_url=self.server_url.rstrip("/"),
            worker_id=self.worker_id.strip(),
        )

    @classmethod
    def from_environment(cls) -> "WorkerConfig":
        return cls(
            server_url=os.getenv("SPECTARR_URL", "http://api:8000"),
            service_token=os.getenv("SPECTARR_SERVICE_TOKEN") or None,
            worker_token=os.getenv("SPECTARR_WORKER_TOKEN") or None,
            worker_id=os.getenv("SPECTARR_WORKER_ID", socket.gethostname()),
            poll_seconds=float(os.getenv("SPECTARR_WEBHOOK_POLL_SECONDS", "3")),
            request_timeout_seconds=float(os.getenv("SPECTARR_WEBHOOK_TIMEOUT_SECONDS", "15")),
            batch_size=int(os.getenv("SPECTARR_WEBHOOK_BATCH_SIZE", "25")),
            max_response_bytes=int(os.getenv("SPECTARR_WEBHOOK_MAX_RESPONSE_BYTES", str(64 * 1024))),
            allow_http_destinations=environment_flag("SPECTARR_WEBHOOK_ALLOW_HTTP"),
        ).validate()
