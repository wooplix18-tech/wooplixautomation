from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .agents import AIClient, build_agents
from .models import (
    Artifact,
    GateResponse,
    HumanGate,
    Project,
    ProjectInput,
    TraceEvent,
    now_iso,
)
from .registry import WORKFLOW_TASKS
from .storage import ProjectStore


class WorkflowError(ValueError):
    pass


class WorkflowEngine:
    def __init__(self, store: ProjectStore, client: AIClient | None = None) -> None:
        self.store = store
        self.agents = build_agents(client)
        self.ai_enabled = bool((client or AIClient()).enabled)

    def create(self, project_input: ProjectInput) -> Project:
        project = Project(input=project_input)
        self.store.save(project)
        try:
            self._requirements(project)
        except Exception as exc:  # preserve a useful state for the API client
            self._fail(project, exc)
        return self.store.save(project)

    def get(self, project_id: str) -> Project:
        project = self.store.get(project_id)
        if project is None:
            raise WorkflowError(f"Project {project_id} was not found.")
        return project

    def list(self) -> list[Project]:
        return self.store.list()

    def _trace(self, project: Project, stage: str, agent: str, status: str, message: str, started: float | None = None, metadata: dict[str, Any] | None = None) -> None:
        project.trace.append(
            TraceEvent(
                stage=stage,
                agent=agent,
                status=status,
                message=message,
                duration_ms=round((time.perf_counter() - started) * 1000) if started else None,
                metadata=metadata or {},
            )
        )

    def _requirements(self, project: Project) -> None:
        started = time.perf_counter()
        self._trace(project, "client_requirements", "requirement_analyst", "started", "Analyzing the client brief.")
        result = self.agents["requirements"].run(project.input)
        project.requirements = result["requirements"]
        project.current_stage = "client_requirements"
        project.metrics["requirement_count"] = len(project.requirements)
        self._trace(
            project,
            "client_requirements",
            "requirement_analyst",
            "completed",
            f"Created a canonical register with {len(project.requirements)} traceable requirements.",
            started,
            {"question_count": len(result["questions"])},
        )
        self._open_gate(
            project,
            "requirements_confirmation",
            "Confirm project scope",
            "Review the extracted requirement register before the specialist agents begin.",
            result["questions"],
            {"requirements": [item.model_dump(mode="json") for item in project.requirements]},
        )

    def _open_gate(self, project: Project, stage: Any, title: str, description: str, questions: list[str], payload: dict[str, Any]) -> HumanGate:
        if project.active_gate_id:
            for existing in project.gates:
                if existing.id == project.active_gate_id and existing.status == "pending":
                    existing.status = "resolved"
                    existing.resolved_at = now_iso()
        gate = HumanGate(stage=stage, title=title, description=description, questions=questions, payload=payload)
        project.gates.append(gate)
        project.active_gate_id = gate.id
        project.status = "requirements_review" if stage == "requirements_confirmation" else "delivery_review" if stage == "delivery_review" else "uat"
        self._trace(project, stage, "human_reviewer", "waiting", title, metadata={"gate_id": gate.id})
        return gate

    def _artifact(self, project: Project, artifact: Artifact) -> None:
        existing = next((index for index, row in enumerate(project.artifacts) if row.kind == artifact.kind), None)
        if existing is None:
            project.artifacts.append(artifact)
        else:
            # A revision is a new version of the same deliverable, so links and
            # audit records can continue to refer to its stable artifact ID.
            artifact.id = project.artifacts[existing].id
            artifact.version = project.artifacts[existing].version + 1
            project.artifacts[existing] = artifact

    def _run_one(self, project: Project, stage: str, agent_name: str, key: str) -> Artifact:
        started = time.perf_counter()
        self._trace(project, stage, agent_name, "started", f"{agent_name.replace('_', ' ').title()} started.")
        artifact = self.agents[key].run(project)
        self._trace(project, stage, agent_name, "completed", f"{artifact.title} generated.", started)
        return artifact

    def _pipeline(self, project: Project) -> None:
        project.status = "running"
        project.error = None
        project.current_stage = "proposal"
        self.store.save(project)
        try:
            # Independent planning artifacts run concurrently, reducing idle time
            # while each specialist still receives the same immutable requirement register.
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="wooplix-agent") as pool:
                proposal_future = pool.submit(self._run_one, project, "proposal", "proposal_generation_agent", "proposal")
                brd_future = pool.submit(self._run_one, project, "brd", "business_requirements_agent", "brd")
                proposal, brd = proposal_future.result(), brd_future.result()
            self._artifact(project, proposal)
            self._artifact(project, brd)

            project.current_stage = "ui_ux_and_implementation"
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="wooplix-specialist") as pool:
                ux_future = pool.submit(self._run_one, project, "ui_ux", "ui_ux_design_agent", "ui_ux")
                dev_future = pool.submit(self._run_one, project, "implementation", "software_development_agent", "implementation")
                ux, implementation = ux_future.result(), dev_future.result()
            self._artifact(project, ux)
            self._artifact(project, implementation)

            project.current_stage = "qa_uat"
            self._artifact(project, self._run_one(project, "qa_uat", "testing_uat_agent", "qa_uat"))
            project.current_stage = "documentation"
            self._artifact(project, self._run_one(project, "documentation", "documentation_training_agent", "documentation"))
            project.metrics.update(
                {
                    "automated_agents_completed": 7,
                    "human_review_hours_target": 18,
                    "traditional_work_hours_target": 500,
                    "ai_assisted_work_hours_target": 40,
                }
            )
            project.current_stage = "delivery_review"
            self._open_gate(
                project,
                "delivery_review",
                "Review the generated delivery pack",
                "Review the proposal, BRD, design, implementation blueprint, QA pack, and training plan. Approve to begin client UAT, or request a bounded revision.",
                [
                    "Does the scope reflect the intended business outcome?",
                    "Are the proposed architecture and UX direction acceptable for detailed implementation?",
                    "Are the open decisions and delivery assumptions clear?",
                ],
                {"artifact_kinds": [artifact.kind for artifact in project.artifacts]},
            )
        except Exception as exc:
            self._fail(project, exc)
        self.store.save(project)

    def _fail(self, project: Project, exc: Exception) -> None:
        project.status = "failed"
        project.error = str(exc)
        self._trace(project, project.current_stage, "workflow", "failed", project.error)

    def respond(self, project_id: str, gate_id: str, response: GateResponse) -> Project:
        project = self.get(project_id)
        gate = next((row for row in project.gates if row.id == gate_id), None)
        if gate is None or gate.status != "pending" or project.active_gate_id != gate_id:
            raise WorkflowError("This human gate is no longer active.")
        gate.status = "resolved"
        gate.decision = response.decision
        gate.feedback = response.feedback
        gate.resolved_at = now_iso()
        project.active_gate_id = None
        project.decisions.append(
            {"gate_id": gate.id, "stage": gate.stage, "decision": response.decision, "feedback": response.feedback, "answers": response.answers, "timestamp": now_iso()}
        )
        self._trace(project, gate.stage, "human_reviewer", "completed", f"Human decision recorded: {response.decision}.")

        if gate.stage == "requirements_confirmation":
            if response.decision == "reject":
                project.status = "cancelled"
            elif response.decision == "request_changes":
                if response.feedback:
                    project.input.brief = f"{project.input.brief}\n\nClarification from client: {response.feedback}"
                self._requirements(project)
            else:
                self._pipeline(project)
        elif gate.stage == "delivery_review":
            if response.decision == "reject":
                project.status = "cancelled"
            elif response.decision == "request_changes":
                project.revision_feedback = response.feedback
                self._pipeline(project)
            else:
                project.current_stage = "uat"
                self._open_gate(
                    project,
                    "uat",
                    "Run client UAT and approve release",
                    "Execute the UAT pack against the agreed scope. Record evidence and approve handover when the release is ready.",
                    [
                        "Have all must-have requirements passed with representative data?",
                        "Are there any blocker or critical defects?",
                        "Can the client accept the training and operational handover pack?",
                    ],
                    {"test_artifact": next((artifact.id for artifact in project.artifacts if artifact.kind == "qa_uat"), None)},
                )
        elif gate.stage == "uat":
            if response.decision in {"pass", "approve"}:
                project.current_stage = "handover"
                handover = self.agents["handover"].run(project)
                self._artifact(project, handover)
                project.status = "completed"
                self._trace(project, "handover", "human_handover_agent", "completed", "Handover record generated and project completed.")
            elif response.decision == "reject":
                project.status = "cancelled"
            else:
                project.status = "uat_failed"
                project.revision_feedback = response.feedback
                self._open_gate(
                    project,
                    "uat",
                    "Resolve UAT findings",
                    "Resolve the recorded UAT findings and submit a new release decision.",
                    ["Summarize the evidence for each failed scenario and confirm the retest result."],
                    {"feedback": response.feedback},
                )
        self.store.save(project)
        return project


def workflow_topology() -> dict[str, Any]:
    nodes = [task.model_dump(mode="json") for task in WORKFLOW_TASKS]
    edges = [
        [dependency, task.id]
        for task in WORKFLOW_TASKS
        for dependency in task.dependencies
    ]
    return {"name": "Wooplix AI SDLC", "nodes": nodes, "edges": edges}
