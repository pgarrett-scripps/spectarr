from __future__ import annotations

import tempfile
import unittest
import json
from dataclasses import replace
from pathlib import Path

from spectarr_agent.api import AgentRegistration, ApiError
from spectarr_agent.config import AgentConfig
from spectarr_agent.service import AcquisitionAgent
from spectarr_agent.state import AgentState


class ServiceApi:
    def __init__(self, offline: bool = False) -> None:
        self.offline = offline
        self.registrations = 0
        self.heartbeats = []

    def register(self, admin_token, name, local_agent_id):
        self.registrations += 1
        return AgentRegistration("agent-1", "agt_secret")

    def heartbeat(self, agent_id, token, status, capacity):
        self.heartbeats.append((agent_id, token, status, capacity))
        return {"id": agent_id}

    def create_upload(self, *args, **kwargs):
        if self.offline:
            raise ApiError(0, "offline")
        return {"id": "upload-1", "state": "completed", "artifact_id": "artifact-1"}


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.now = 100.0

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, dry_run: bool = False) -> AgentConfig:
        return AgentConfig(
            "http://localhost:8000",
            (self.root / "incoming",),
            self.root / "queue.db",
            api_key="admin-key",
            experiment_id="experiment-1",
            stability_seconds=10,
            retry_base_seconds=2,
            retry_max_seconds=10,
            dry_run=dry_run,
        ).validate()

    def test_stable_acquisition_is_queued_and_offline_upload_is_retained(self) -> None:
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "run.raw").write_bytes(b"raw-data")
        config = self.config()
        state = AgentState(config.state_db)
        try:
            api = ServiceApi(offline=True)
            agent = AcquisitionAgent(config, state, api, clock=lambda: self.now, sleep=lambda _: None)
            self.assertEqual(agent.scan_once(), 0)
            self.now += 10
            self.assertEqual(agent.scan_once(), 1)
            self.assertTrue(agent.upload_one())
            self.assertEqual(state.counts()["retry"], 1)
            queued = state.connection.execute("SELECT run_json FROM upload_queue").fetchone()
            self.assertIn("experiment-1", queued["run_json"])
        finally:
            state.close()

    def test_backend_managed_inbox_queues_run_without_experiment(self) -> None:
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "inbox.raw").write_bytes(b"raw-data")
        config = replace(self.config(), experiment_id=None).validate()
        state = AgentState(config.state_db)
        try:
            agent = AcquisitionAgent(config, state, ServiceApi(), clock=lambda: self.now, sleep=lambda _: None)
            self.assertEqual(agent.scan_once(), 0)
            self.now += 10
            self.assertEqual(agent.scan_once(), 1)
            queued = state.connection.execute("SELECT run_json FROM upload_queue").fetchone()
            self.assertIsNone(json.loads(queued["run_json"])["experiment_id"])
        finally:
            state.close()

    def test_registration_token_is_persisted_and_heartbeat_reports_queue(self) -> None:
        incoming = self.root / "incoming"
        incoming.mkdir()
        config = self.config()
        state = AgentState(config.state_db)
        try:
            api = ServiceApi()
            agent = AcquisitionAgent(config, state, api, clock=lambda: self.now, sleep=lambda _: None)
            agent.heartbeat_if_due(force=True)
            agent.heartbeat_if_due(force=True)
            self.assertEqual(api.registrations, 1)
            self.assertEqual(state.metadata("agent_token"), "agt_secret")
            self.assertEqual(len(api.heartbeats), 2)
        finally:
            state.close()

    def test_dashboard_enrollment_skips_registration(self) -> None:
        incoming = self.root / "incoming"
        incoming.mkdir()
        config = replace(
            self.config(),
            api_key=None,
            agent_id="agent-from-dashboard",
            agent_token="agt_dashboard_secret",
        ).validate()
        state = AgentState(config.state_db)
        try:
            api = ServiceApi()
            agent = AcquisitionAgent(config, state, api, clock=lambda: self.now, sleep=lambda _: None)
            agent.heartbeat_if_due(force=True)
            self.assertEqual(api.registrations, 0)
            self.assertEqual(state.metadata("agent_id"), "agent-from-dashboard")
            self.assertEqual(state.metadata("agent_token"), "agt_dashboard_secret")
            self.assertEqual(api.heartbeats[0][0:2], ("agent-from-dashboard", "agt_dashboard_secret"))
        finally:
            state.close()

    def test_updated_dashboard_credential_replaces_saved_token(self) -> None:
        incoming = self.root / "incoming"
        incoming.mkdir()
        state = AgentState(self.root / "queue.db")
        try:
            state.set_metadata("agent_id", "agent-from-dashboard")
            state.set_metadata("agent_token", "agt_old")
            config = replace(
                self.config(),
                api_key=None,
                agent_id="agent-from-dashboard",
                agent_token="agt_rotated",
            ).validate()
            api = ServiceApi()
            agent = AcquisitionAgent(config, state, api, clock=lambda: self.now, sleep=lambda _: None)
            agent.heartbeat_if_due(force=True)
            self.assertEqual(state.metadata("agent_token"), "agt_rotated")
            self.assertEqual(api.heartbeats[0][1], "agt_rotated")
        finally:
            state.close()

    def test_dry_run_hashes_but_does_not_queue_or_register(self) -> None:
        incoming = self.root / "incoming"
        incoming.mkdir()
        (incoming / "run.mzML").write_text("data")
        config = self.config(dry_run=True)
        state = AgentState(config.state_db)
        try:
            api = ServiceApi()
            agent = AcquisitionAgent(config, state, api, clock=lambda: self.now, sleep=lambda _: None)
            agent.scan_once()
            self.now += 10
            agent.scan_once()
            self.assertEqual(state.counts(), {})
            self.assertEqual(api.registrations, 0)
        finally:
            state.close()


if __name__ == "__main__":
    unittest.main()
