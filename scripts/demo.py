"""Run the complete offline workflow and print its delivery summary."""

from app.models import GateResponse, ProjectInput
from app.storage import ProjectStore
from app.workflow import WorkflowEngine


def main() -> None:
    engine = WorkflowEngine(ProjectStore("data/demo.db"))
    project = engine.create(
        ProjectInput(
            project_name="AI customer support hub",
            client_name="Demo client",
            project_type="software_product",
            brief="Build a support workspace where customers submit requests, agents collaborate on replies, and managers see resolution analytics.",
            goals=["Reduce first-response time", "Make support quality measurable"],
            target_users=["Customers", "Support agents", "Managers"],
            platforms=["Responsive web"],
            integrations=["Email", "CRM"],
        )
    )
    project = engine.respond(project.id, project.active_gate_id, GateResponse(decision="approve"))
    project = engine.respond(project.id, project.active_gate_id, GateResponse(decision="approve"))
    project = engine.respond(project.id, project.active_gate_id, GateResponse(decision="pass"))
    print(f"{project.input.project_name}: {project.status}")
    print(f"Artifacts: {', '.join(artifact.kind for artifact in project.artifacts)}")


if __name__ == "__main__":
    main()
