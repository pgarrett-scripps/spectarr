from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from spectarr_agent.discovery import HashedAcquisition
from spectarr_agent.state import AgentState


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = AgentState(self.root / "queue.db")

    def tearDown(self) -> None:
        self.state.close()
        self.temporary.cleanup()

    def acquisition(self, checksum: str = "a" * 64) -> HashedAcquisition:
        path = self.root / "sample.mzML"
        path.write_bytes(b"data")
        return HashedAcquisition(path, "file", "mzML", checksum, 4, "f:4:1")

    def test_observation_must_remain_stable_across_time(self) -> None:
        path = self.root / "sample.raw"
        self.assertFalse(self.state.observe(path, "one", 100, 10))
        self.assertFalse(self.state.observe(path, "two", 105, 10))
        self.assertFalse(self.state.observe(path, "two", 114, 10))
        self.assertTrue(self.state.observe(path, "two", 115, 10))

    def test_queue_deduplicates_checksum_and_persists_restart(self) -> None:
        acquisition = self.acquisition()
        self.state.observe(acquisition.path, acquisition.signature, 0, 0)
        self.assertTrue(self.state.enqueue(acquisition, run_id="run-1", now=1))
        self.assertFalse(self.state.enqueue(acquisition, run_id="run-1", now=2))
        item = self.state.claim_next(now=3)
        self.assertEqual(item.attempts, 1)
        self.state.close()
        self.state = AgentState(self.root / "queue.db")
        recovered = self.state.claim_next(now=time.time() + 1)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.id, item.id)

    def test_retry_backoff_and_terminal_completion(self) -> None:
        acquisition = self.acquisition()
        self.state.observe(acquisition.path, acquisition.signature, 0, 0)
        self.state.enqueue(acquisition, run={"experiment_id": "e", "name": "sample"}, now=1)
        item = self.state.claim_next(now=2)
        self.state.retry(item.id, "offline", 10, now=2)
        self.assertIsNone(self.state.claim_next(now=11))
        retried = self.state.claim_next(now=12)
        self.state.complete(retried.id, "artifact-1")
        self.assertEqual(self.state.get(retried.id).status, "complete")

    def test_local_agent_identity_is_stable(self) -> None:
        first = self.state.local_agent_id()
        self.assertEqual(first, self.state.local_agent_id())

    def test_failed_checksum_can_be_requeued_after_source_is_verified_again(self) -> None:
        acquisition = self.acquisition()
        self.state.observe(acquisition.path, acquisition.signature, 0, 0)
        self.state.enqueue(acquisition, run_id="run-1", now=1)
        item = self.state.claim_next(now=2)
        self.state.retry(item.id, "source changed", 0, permanent=True, now=3)
        self.assertTrue(self.state.enqueue(acquisition, run_id="run-1", now=4))
        retried = self.state.claim_next(now=5)
        self.assertEqual(retried.id, item.id)
        self.assertEqual(retried.attempts, 1)


if __name__ == "__main__":
    unittest.main()
