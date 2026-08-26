from __future__ import annotations

import logging
import tempfile
import unittest
from pathlib import Path

from spectarr_agent.cli import configure_logging
from spectarr_agent.config import AgentConfig


class CliTests(unittest.TestCase):
    def test_rotating_file_logging_writes_to_configured_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = AgentConfig(
                "https://spectarr.example",
                (root,),
                root / "queue.db",
                log_file=root / "logs" / "agent.log",
                log_max_bytes=64 * 1024,
                log_backup_count=2,
            ).validate()
            configure_logging(config, "INFO")
            root_logger = logging.getLogger()
            try:
                logging.getLogger("agent-test").info("agent log is ready")
                for handler in root_logger.handlers:
                    handler.flush()
                self.assertIn("agent log is ready", config.log_file.read_text())
            finally:
                for handler in root_logger.handlers:
                    handler.close()
                root_logger.handlers.clear()


if __name__ == "__main__":
    unittest.main()
