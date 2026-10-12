import tempfile
import unittest
from pathlib import Path

from app.models import GateResponse, ProjectInput
from app.storage import ProjectStore
from app.workflow import WorkflowEngine, workflow_topology


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
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
        self.assertEqual(project.metrics["traditional_work_hours_target"], 500)
        self.assertEqual(project.metrics["ai_assisted_work_hours_target"], 40)
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
        self.assertEqual(len(topology["edges"]), len(topology["nodes"]) - 1)


if __name__ == "__main__":
    unittest.main()
