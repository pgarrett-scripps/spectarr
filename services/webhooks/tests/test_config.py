from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from spectarr_webhooks.config import WorkerConfig


class ConfigTests(unittest.TestCase):
    def test_accepts_exactly_one_authentication_method(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            WorkerConfig(worker_id="worker").validate()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            WorkerConfig(service_token="a", worker_token="b", worker_id="worker").validate()
        config = WorkerConfig(service_token="service", worker_id="worker").validate()
        self.assertEqual(config.service_token, "service")

    def test_loads_legacy_worker_environment(self) -> None:
        values = {
            "SPECTARR_WORKER_TOKEN": "legacy",
            "SPECTARR_WORKER_ID": "hooks-01",
            "SPECTARR_WEBHOOK_ALLOW_HTTP": "true",
        }
        with patch.dict(os.environ, values, clear=True):
            config = WorkerConfig.from_environment()
        self.assertEqual(config.worker_token, "legacy")
        self.assertTrue(config.allow_http_destinations)


if __name__ == "__main__":
    unittest.main()
