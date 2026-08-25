from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectarr_agent.config import load_config


class ConfigTests(unittest.TestCase):
    def test_loads_toml_and_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config_file = root / "agent.toml"
            config_file.write_text(
                """
[agent]
server_url = "http://from-file:8000"
watch_paths = ["incoming"]
state_db = "queue.db"
experiment_id = "experiment-1"
stability_seconds = 90
"""
            )
            with patch.dict(os.environ, {"SPECTARR_URL": "https://spectarr.example"}, clear=True):
                config = load_config(config_file)
            self.assertEqual(config.server_url, "https://spectarr.example")
            self.assertEqual(config.stability_seconds, 90)
            self.assertEqual(config.experiment_id, "experiment-1")

    def test_accepts_backend_managed_inbox_and_rejects_two_run_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = load_config(overrides={"watch_paths": [root]})
            self.assertIsNone(config.run_id)
            self.assertIsNone(config.experiment_id)
            with self.assertRaisesRegex(ValueError, "at most one"):
                load_config(
                    overrides={"watch_paths": [root], "run_id": "r", "experiment_id": "e"}
                )

    def test_loads_pre_enrolled_dashboard_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(
                os.environ,
                {
                    "SPECTARR_AGENT_ID": "agent-1",
                    "SPECTARR_AGENT_TOKEN": "agt_secret",
                },
                clear=True,
            ):
                config = load_config(
                    overrides={
                        "watch_paths": [root],
                        "experiment_id": "experiment-1",
                    }
                )
            self.assertEqual(config.agent_id, "agent-1")
            self.assertEqual(config.agent_token, "agt_secret")

    def test_requires_complete_pre_enrollment_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "configured together"):
                load_config(
                    overrides={
                        "watch_paths": [root],
                        "experiment_id": "experiment-1",
                        "agent_id": "agent-1",
                    }
                )


if __name__ == "__main__":
    unittest.main()
