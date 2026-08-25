from __future__ import annotations

import unittest

from spectarr_webhooks.api import ApiError, ClaimedDelivery
from spectarr_webhooks.config import WorkerConfig
from spectarr_webhooks.delivery import DeliveryOutcome
from spectarr_webhooks.worker import WebhookWorker, lease_allows_delivery


def claim(delivery_id: str = "delivery-1", lease: str = "2099-01-01T00:00:00+00:00"):
    return ClaimedDelivery(
        id=delivery_id,
        url="https://receiver.test/hooks",
        body="{}",
        headers={},
        signing_secret="whsec_secret",
        attempt=1,
        lease_expires_at=lease,
    )


class FakeApi:
    def __init__(self) -> None:
        self.pending = [{"id": "delivery-1"}]
        self.retry = []
        self.claims = {"delivery-1": claim()}
        self.updates = []

    def list_ready(self, status, limit):
        return list(self.pending if status == "pending" else self.retry)[:limit]

    def claim(self, delivery_id):
        value = self.claims[delivery_id]
        if isinstance(value, Exception):
            raise value
        return value

    def update(self, delivery_id, status, *, response_status=None, error_detail=None):
        self.updates.append((delivery_id, status, response_status, error_detail))
        return {"id": delivery_id, "status": status}


class FakeSender:
    def __init__(self, outcome: DeliveryOutcome) -> None:
        self.outcome = outcome
        self.calls = []

    def send(self, delivery, timestamp):
        self.calls.append((delivery, timestamp))
        return self.outcome


class WorkerTests(unittest.TestCase):
    def config(self) -> WorkerConfig:
        return WorkerConfig(
            service_token="token",
            worker_id="worker-1",
            request_timeout_seconds=10,
        ).validate()

    def test_claims_sends_and_records_delivery(self) -> None:
        api = FakeApi()
        sender = FakeSender(DeliveryOutcome("delivered", response_status=204))
        worker = WebhookWorker(self.config(), api, sender, clock=lambda: 1_700_000_000)
        self.assertTrue(worker.process_one())
        self.assertEqual(sender.calls[0][1], 1_700_000_000)
        self.assertEqual(api.updates[0], ("delivery-1", "delivered", 204, None))

    def test_atomic_claim_conflict_skips_to_another_candidate(self) -> None:
        api = FakeApi()
        api.pending.append({"id": "delivery-2"})
        api.claims["delivery-1"] = ApiError(409, "already claimed")
        api.claims["delivery-2"] = claim("delivery-2")
        sender = FakeSender(DeliveryOutcome("delivered", response_status=200))
        worker = WebhookWorker(self.config(), api, sender, clock=lambda: 1_700_000_000)
        self.assertTrue(worker.process_one())
        self.assertEqual(sender.calls[0][0].id, "delivery-2")

    def test_short_or_invalid_lease_retries_without_external_post(self) -> None:
        api = FakeApi()
        api.claims["delivery-1"] = claim(lease="2023-11-14T22:13:25+00:00")
        sender = FakeSender(DeliveryOutcome("delivered", response_status=200))
        worker = WebhookWorker(self.config(), api, sender, clock=lambda: 1_700_000_000)
        self.assertTrue(worker.process_one())
        self.assertEqual(sender.calls, [])
        self.assertEqual(api.updates[0][1], "retry")

    def test_pending_and_retry_results_are_deduplicated(self) -> None:
        api = FakeApi()
        api.retry = [{"id": "delivery-1"}]
        sender = FakeSender(DeliveryOutcome("delivered", response_status=200))
        worker = WebhookWorker(self.config(), api, sender, clock=lambda: 1_700_000_000)
        worker.process_one()
        self.assertEqual(len(sender.calls), 1)

    def test_no_ready_deliveries_returns_false(self) -> None:
        api = FakeApi()
        api.pending = []
        sender = FakeSender(DeliveryOutcome("delivered"))
        self.assertFalse(WebhookWorker(self.config(), api, sender).process_one())

    def test_lease_parser_requires_sufficient_utc_window(self) -> None:
        self.assertTrue(lease_allows_delivery("2023-11-14T22:14:00Z", 1_700_000_000, 10))
        self.assertFalse(lease_allows_delivery("invalid", 1_700_000_000, 10))


if __name__ == "__main__":
    unittest.main()
