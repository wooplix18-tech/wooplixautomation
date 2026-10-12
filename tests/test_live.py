import importlib
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.live import LiveRunner, summary
from app.models import GateResponse, ProjectInput
from app.storage import ProjectStore
from app.workflow import WorkflowEngine, WorkflowError


class LiveRunTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {"GROQ_API_KEY": "", "OPENAI_API_KEY": "", "PROPOSAL_ENGINE": "template", "VERCEL": "1", "DATABASE_URL": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = ProjectStore(Path(self.folder.name) / "run.db")
        self.engine = WorkflowEngine(self.store)
        self.runner = LiveRunner(self.engine)
        self.input = ProjectInput(project_name="Live portal", brief="Build a portal for customers to track requests and receive notifications.", execution_mode="automatic")

    def drain(self, project):
        for _ in range(15):
            if project.status != "queued":
                return project
            project = self.runner.execute_wave(project.id)
        self.fail("Run did not stop")

    def test_create_returns_before_any_agent_runs_and_restores_after_restart(self):
        with patch.object(self.engine.agents["requirements"], "run", side_effect=AssertionError("Must not execute during queueing")):
            project = self.runner.create(self.input)
        self.assertEqual(project.status, "queued")
        self.assertEqual(project.requirements, [])
        self.runner = LiveRunner(WorkflowEngine(ProjectStore(self.store.path)))
        project = self.drain(project)
        self.assertEqual(project.status, "pack_ready")
        self.assertEqual(len(project.artifacts), 7)
        self.assertEqual(project.live_run.nodes["uat"].status, "skipped")
        self.assertEqual(project.live_run.nodes["proposal"].generation_mode, "template")
        self.assertTrue(all(n.output_data for n in project.live_run.nodes.values()))
        self.assertEqual(sorted(e.sequence for e in project.live_run.events), list(range(1, project.live_run.event_sequence + 1)))

    def test_partial_parallel_output_persists_and_retry_only_runs_failed_node(self):
        project = self.runner.execute_wave(self.runner.create(self.input).id)
        with patch.object(self.engine.agents["brd"], "run", side_effect=ValueError("secret provider info")):
            project = self.runner.execute_wave(project.id)
        self.assertEqual(project.status, "failed")
        self.assertEqual(project.live_run.nodes["proposal"].status, "completed")
        self.assertEqual(project.live_run.nodes["brd"].status, "failed")
        self.assertNotIn("secret provider info", project.model_dump_json())
        proposal = project.artifacts[0].model_dump()
        project = self.runner.retry(project.id)
        with patch.object(self.engine.agents["proposal"], "run", side_effect=AssertionError("Repeated completed node")):
            project = self.drain(project)
        self.assertEqual(project.status, "pack_ready")
        self.assertEqual(project.live_run.nodes["brd"].attempt, 2)
        self.assertEqual(next(a for a in project.artifacts if a.kind == "proposal").model_dump(), proposal)
        self.assertEqual(len(project.live_run.history), 1)

    def test_running_events_visible_from_second_store_and_duplicate_claim_blocked(self):
        entered, release = threading.Event(), threading.Event()
        real = self.engine.agents["requirements"].run
        def slow(inp):
            entered.set()
            release.wait(5)
            return real(inp)
        project = self.runner.create(self.input)
        other = LiveRunner(WorkflowEngine(ProjectStore(self.store.path)))
        with patch.object(self.engine.agents["requirements"], "run", side_effect=slow), ThreadPoolExecutor() as pool:
            future = pool.submit(self.runner.execute_wave, project.id)
            try:
                self.assertTrue(entered.wait(3))
                current = other.engine.get(project.id)
                self.assertEqual(current.live_run.nodes["client_requirements"].status, "running")
                self.assertTrue(any(e.kind == "started" for e in current.live_run.events))
                with self.assertRaises(WorkflowError):
                    other.execute_wave(project.id)
            finally:
                release.set()
            self.assertEqual(future.result().status, "queued")

    def test_cancel_fences_inflight_result(self):
        entered, release = threading.Event(), threading.Event()
        real = self.engine.agents["requirements"].run
        def slow(inp):
            entered.set()
            release.wait(5)
            return real(inp)
        project = self.runner.create(self.input)
        with patch.object(self.engine.agents["requirements"], "run", side_effect=slow), ThreadPoolExecutor() as pool:
            future = pool.submit(self.runner.execute_wave, project.id)
            try:
                self.assertTrue(entered.wait(3))
                self.runner.cancel(project.id)
            finally:
                release.set()
            self.assertEqual(future.result().status, "cancelled")
        persisted = self.engine.get(project.id)
        self.assertEqual(persisted.requirements, [])
        self.assertEqual(persisted.artifacts, [])
        self.assertTrue(all(n.status == "cancelled" for n in persisted.live_run.nodes.values()))

    def test_expired_lease_can_be_explicitly_recovered(self):
        project = self.runner.create(self.input)
        with patch("app.live.time.time", return_value=1):
            self.runner._claim(project.id, "dead-worker")
        project = self.engine.get(project.id)
        self.assertTrue(summary(project)["recoverable"])
        with self.assertRaises(WorkflowError):
            self.runner.execute_wave(project.id)
        project = self.drain(self.runner.retry(project.id))
        self.assertEqual(project.status, "pack_ready")
        self.assertEqual(project.live_run.nodes["client_requirements"].attempt, 2)

    def test_reviewed_run_stops_at_each_gate_and_failed_uat_blocks_handover(self):
        self.input.execution_mode = "reviewed"
        project = self.drain(self.runner.create(self.input))
        self.assertEqual(project.status, "requirements_review")
        self.assertEqual(project.artifacts, [])
        gate_id = project.active_gate_id
        project = self.engine.respond(project.id, gate_id, GateResponse(decision="approve"))
        with self.assertRaises(WorkflowError):
            self.engine.respond(project.id, gate_id, GateResponse(decision="approve"))
        project = self.drain(project)
        self.assertEqual(project.status, "delivery_review")
        project = self.runner.respond(project.id, project.active_gate_id, GateResponse(decision="approve"))
        self.assertEqual(project.status, "uat")
        project = self.runner.respond(project.id, project.active_gate_id, GateResponse(decision="fail", feedback="Broken login"))
        self.assertEqual(project.status, "uat_failed")
        self.assertFalse(any(a.kind == "handover" for a in project.artifacts))
        project = self.drain(self.runner.respond(project.id, project.active_gate_id, GateResponse(decision="pass")))
        self.assertEqual(project.status, "completed")

    def test_revision_invalidates_downstream_outputs_and_retains_history(self):
        self.input.execution_mode = "reviewed"
        project = self.drain(self.runner.create(self.input))
        project = self.drain(self.runner.respond(project.id, project.active_gate_id, GateResponse(decision="approve")))
        project = self.runner.respond(project.id, project.active_gate_id, GateResponse(decision="request_changes", feedback="Clarify the notification channel"))
        self.assertEqual(project.artifacts, [])
        self.assertTrue(any(n.id == "proposal" and n.output_data for n in project.live_run.history))
        project = self.drain(project)
        self.assertEqual(project.status, "delivery_review")
        self.assertTrue(all(a.content["revision_feedback"] for a in project.artifacts))

    def test_retries_bounded(self):
        project = self.runner.create(self.input)
        with patch.object(self.engine.agents["requirements"], "run", side_effect=RuntimeError("fail")):
            for _ in range(3):
                project = self.runner.execute_wave(project.id)
                if project.live_run.nodes["client_requirements"].attempt < 3:
                    project = self.runner.retry(project.id)
        with self.assertRaises(WorkflowError):
            self.runner.retry(project.id)

    def test_rejection_cannot_be_bypassed_by_retry(self):
        self.input.execution_mode = "reviewed"
        project = self.drain(self.runner.create(self.input))
        project = self.runner.respond(project.id, project.active_gate_id, GateResponse(decision="reject"))
        self.assertEqual(project.status, "cancelled")
        self.assertFalse(summary(project)["retryable"])
        with self.assertRaises(WorkflowError):
            self.runner.retry(project.id)

    def test_event_cursor_and_node_input_output_inspection(self):
        project = self.runner.create(self.input)
        cursor = project.live_run.event_sequence
        project = self.drain(project)
        data = summary(project, cursor)
        self.assertTrue(all(e["sequence"] > cursor for e in data["events"]))
        self.assertNotIn("input_data", data["nodes"]["brd"])
        node = self.runner.inspect_node(project.id, "brd")
        self.assertEqual(node["execution"]["input_data"]["project"]["brief"], self.input.brief)
        self.assertEqual(node["execution"]["output_data"]["kind"], "brd")

    def test_live_api_stream_replay_and_vercel_requires_durable_store(self):
        main = importlib.import_module("app.main")
        with patch.multiple(main, store=self.store, engine=self.engine), TestClient(main.app) as client:
            response = client.post("/api/runs", json=self.input.model_dump())
            self.assertEqual(response.status_code, 503)
            with patch.dict("os.environ", {"VERCEL": "0"}):
                response = client.post("/api/runs", json=self.input.model_dump())
            self.assertEqual(response.status_code, 201)
            project_id = response.json()["id"]
            self.assertEqual(response.json()["status"], "queued")
            project = self.drain(self.engine.get(project_id))
            cursor = project.live_run.event_sequence - 1
            response = client.get(f"/api/projects/{project_id}/events", headers={"Last-Event-ID": str(cursor)})
            self.assertEqual(response.status_code, 200)
            self.assertIn("text/event-stream", response.headers["content-type"])
            self.assertIn("event: snapshot", response.text)
            self.assertIn("event: idle", response.text)
            self.assertNotIn('"kind": "queued"', response.text)
            self.assertEqual(client.get("/workflow").status_code, 200)


if __name__ == "__main__":
    unittest.main()
