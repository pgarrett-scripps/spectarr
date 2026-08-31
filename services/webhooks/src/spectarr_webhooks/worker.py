"""Lease-based webhook delivery orchestration."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from .api import ApiError, ClaimedDelivery, SpectarrWebhookApi
from .config import WorkerConfig
from .delivery import DeliveryOutcome, WebhookSender

LOGGER = logging.getLogger(__name__)


class ApiProtocol(Protocol):
    def list_ready(self, status: str, limit: int) -> list[dict]: ...
    def claim(self, delivery_id: str) -> ClaimedDelivery: ...
    def update(
        self,
        delivery_id: str,
        status: str,
        *,
        response_status: int | None = None,
        error_detail: str | None = None,
    ) -> dict: ...


class SenderProtocol(Protocol):
    def send(self, delivery: ClaimedDelivery, timestamp: int) -> DeliveryOutcome: ...


class WebhookWorker:
    def __init__(
        self,
        config: WorkerConfig,
        api: ApiProtocol,
        sender: SenderProtocol,
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.api = api
        self.sender = sender
        self.clock = clock
        self.sleep = sleep

    def process_one(self) -> bool:
        candidates = self._ready_candidates()
        for candidate in candidates:
            delivery_id = str(candidate.get("id", ""))
            if not delivery_id:
                continue
            try:
                claimed = self.api.claim(delivery_id)
            except ApiError as claim_error:
                if claim_error.status == 409:
                    continue
                raise
            except (KeyError, TypeError, ValueError) as malformed:
                self._safe_update(delivery_id, DeliveryOutcome("failed", error=f"Invalid claim: {malformed}"))
                return True
            if not lease_allows_delivery(
                claimed.lease_expires_at,
                self.clock(),
                self.config.request_timeout_seconds + 2,
            ):
                self._safe_update(
                    delivery_id,
                    DeliveryOutcome("retry", error="Delivery lease is too short for the configured timeout"),
                )
                return True
            outcome = self.sender.send(claimed, int(self.clock()))
            self._safe_update(delivery_id, outcome)
            return True
        return False

    def _ready_candidates(self) -> list[dict]:
        seen: set[str] = set()
        ready: list[dict] = []
        for status in ("pending", "retry"):
            for candidate in self.api.list_ready(status, self.config.batch_size):
                identifier = str(candidate.get("id", ""))
                if identifier and identifier not in seen:
                    seen.add(identifier)
                    ready.append(candidate)
        return ready

    def _safe_update(self, delivery_id: str, outcome: DeliveryOutcome) -> None:
        try:
            self.api.update(
                delivery_id,
                outcome.status,
                response_status=outcome.response_status,
                error_detail=outcome.error,
            )
        except ApiError as update_error:
            LOGGER.error("Could not record webhook result for %s: %s", delivery_id, update_error)

    def run_forever(self) -> None:
        LOGGER.info("Webhook worker %s started", self.config.worker_id)
        while True:
            try:
                handled = self.process_one()
            except ApiError as api_error:
                LOGGER.warning("Webhook queue is unavailable: %s", api_error)
                handled = False
            if not handled:
                self.sleep(self.config.poll_seconds)


def lease_allows_delivery(raw_expiry: str, now: float, required_seconds: float) -> bool:
    try:
        normalized = raw_expiry.replace("Z", "+00:00")
        expires = datetime.fromisoformat(normalized)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return expires.timestamp() - now >= required_seconds
    except (ValueError, TypeError, OverflowError):
        return False


def build_worker(config: WorkerConfig) -> WebhookWorker:
    api = SpectarrWebhookApi(
        config.server_url,
        config.worker_id,
        service_token=config.service_token,
        worker_token=config.worker_token,
        timeout_seconds=config.request_timeout_seconds,
    )
    sender = WebhookSender(
        timeout_seconds=config.request_timeout_seconds,
        max_response_bytes=config.max_response_bytes,
        allow_http=config.allow_http_destinations,
        allow_private_networks=config.allow_private_networks,
    )
    return WebhookWorker(config, api, sender)
