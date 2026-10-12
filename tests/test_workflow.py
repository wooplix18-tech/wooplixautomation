import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.models import GateResponse, ProjectInput
from app.storage import ProjectStore, StorageConflict
from app.workflow import WorkflowEngine, WorkflowError, workflow_topology


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.environment = patch.dict("os.environ", {"OPENAI_API_KEY": "", "GROQ_API_KEY": ""})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = WorkflowEngine(ProjectStore(Path(self.temp_dir.name) / "test.db"))
        self.input = ProjectInput(
            project_name="Partner onboarding portal",
            client_name="Acme Services",
            project_type="website",
            brief=(
                "Create a responsive portal where partners can register, submit onboarding information, "
                "track approval status, and receive notifications. The operations team needs a dashboard."
            ),
            goals=["Reduce manual onboarding effort", "Give partners clear status visibility"],
            target_users=["Partners", "Operations team"],
            platforms=["Web"],
            integrations=["Email provider"],
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_pipeline_stops_for_scope_confirmation(self) -> None:
        project = self.engine.create(self.input)
        self.assertEqual(project.status, "requirements_review")
        self.assertIsNotNone(project.active_gate_id)
        self.assertGreaterEqual(len(project.requirements), 3)
        self.assertEqual(len(project.artifacts), 0)

    def test_full_lifecycle_creates_traceable_artifacts(self) -> None:
        project = self.engine.create(self.input)
        project = self.engine.respond(
            project.id,
            project.active_gate_id,
            GateResponse(decision="approve", feedback="Scope confirmed."),
        )
        self.assertEqual(project.status, "delivery_review")
        self.assertEqual(
            {artifact.kind for artifact in project.artifacts},
            {"proposal", "brd", "ui_ux", "implementation", "qa_uat", "documentation"},
        )
        self.assertTrue(any(event.agent == "proposal_generation_agent" for event in project.trace))

        project = self.engine.respond(
            project.id,
            project.active_gate_id,
            GateResponse(decision="approve", feedback="Delivery pack approved."),
        )
        self.assertEqual(project.status, "uat")
        project = self.engine.respond(
            project.id,
            project.active_gate_id,
            GateResponse(decision="pass", feedback="All UAT scenarios passed."),
        )
        self.assertEqual(project.status, "completed")
        self.assertIsNone(project.active_gate_id)
        self.assertEqual({artifact.kind for artifact in project.artifacts}, {"proposal", "brd", "ui_ux", "implementation", "qa_uat", "documentation", "handover"})


    def test_invalid_gate_decision_does_not_approve_scope(self) -> None:
        project = self.engine.create(self.input)
        for decision in ("fail", "pass"):
            with self.assertRaises(WorkflowError):
                self.engine.respond(project.id, project.active_gate_id, GateResponse(decision=decision))
        persisted = self.engine.get(project.id)
        self.assertEqual(persisted.active_gate.status, "pending")
        self.assertEqual(persisted.artifacts, [])
        self.assertEqual(persisted.decisions, [])

    def test_failed_uat_never_creates_handover(self) -> None:
        project = self.engine.create(self.input)
        for _ in range(2):
            project = self.engine.respond(project.id, project.active_gate_id, GateResponse(decision="approve"))
        previous_gate = project.active_gate_id
        project = self.engine.respond(project.id, previous_gate, GateResponse(decision="fail", feedback="Critical defect."))
        self.assertEqual(project.status, "uat_failed")
        self.assertNotEqual(project.active_gate_id, previous_gate)
        self.assertNotIn("handover", {a.kind for a in project.artifacts})

    def test_stale_snapshot_cannot_overwrite_a_decision(self) -> None:
        project = self.engine.create(self.input)
        other_store = ProjectStore(self.engine.store.path)
        stale = other_store.get(project.id)
        project = self.engine.respond(project.id, project.active_gate_id, GateResponse(decision="reject"))
        with self.assertRaises(StorageConflict):
            other_store.save(stale)
        self.assertEqual(other_store.get(project.id).status, "cancelled")

    def test_duplicate_decision_is_rejected(self) -> None:
        project = self.engine.create(self.input)
        gate_id = project.active_gate_id
        self.engine.respond(project.id, gate_id, GateResponse(decision="approve"))
        with self.assertRaises(WorkflowError):
            self.engine.respond(project.id, gate_id, GateResponse(decision="approve"))

    def test_revision_feedback_reaches_provider_and_artifacts(self) -> None:
        project = self.engine.create(self.input)
        project = self.engine.respond(project.id, project.active_gate_id, GateResponse(decision="approve"))
        client = self.engine.agents["proposal"].client
        with patch.object(client, "json", return_value={}) as provider:
            project = self.engine.respond(project.id, project.active_gate_id, GateResponse(decision="request_changes", feedback="Add notification evidence."))
        self.assertTrue(any('Add notification evidence.' in call.args[1] for call in provider.call_args_list))
        self.assertTrue(all(a.content["revision_feedback"] == "Add notification evidence." for a in project.artifacts))

    def test_malformed_optional_ai_fields_do_not_fail_pipeline(self) -> None:
        client = self.engine.agents["proposal"].client
        malformed = {key: None for key in ["additional_questions", "non_functional_requirements", "design_checks", "technical_decisions", "uat_scenarios", "training_tips"]}
        with patch.object(client, "json", return_value=malformed):
            project = self.engine.create(self.input)
            project = self.engine.respond(project.id, project.active_gate_id, GateResponse(decision="approve"))
        self.assertEqual(project.status, "delivery_review")

    def test_revision_replaces_artifacts_and_increments_version(self) -> None:
        project = self.engine.create(self.input)
        project = self.engine.respond(project.id, project.active_gate_id, GateResponse(decision="approve"))
        proposal = next(artifact for artifact in project.artifacts if artifact.kind == "proposal")
        project = self.engine.respond(
            project.id,
            project.active_gate_id,
            GateResponse(decision="request_changes", feedback="Clarify the partner notification scope."),
        )
        revised = next(artifact for artifact in project.artifacts if artifact.kind == "proposal")
        self.assertEqual(revised.id, proposal.id)
        self.assertEqual(revised.version, 2)
        self.assertEqual(project.status, "delivery_review")

    def test_topology_covers_the_complete_sdlc(self) -> None:
        topology = workflow_topology()
        labels = {node["label"] for node in topology["nodes"]}
        self.assertIn("Proposal generation", labels)
        self.assertIn("Client handover", labels)
        self.assertIn(["client_requirements", "proposal"], topology["edges"])
        self.assertIn(["client_requirements", "brd"], topology["edges"])
        self.assertIn(["proposal", "implementation"], topology["edges"])
        self.assertIn(["brd", "implementation"], topology["edges"])


if __name__ == "__main__":
    unittest.main()
