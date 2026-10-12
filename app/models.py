from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


ProjectType = Literal["website", "mobile_app", "software_product", "internal_tool"]
ProjectStatus = Literal[
    "queued",
    "requirements_review",
    "proposal_review",
    "running",
    "delivery_review",
    "uat",
    "uat_failed",
    "completed",
    "cancelled",
    "failed",
    "pack_ready",
]
ArtifactKind = Literal[
    "proposal",
    "brd",
    "ui_ux",
    "implementation",
    "qa_uat",
    "documentation",
    "handover",
]
GateStage = Literal["requirements_confirmation", "proposal_confirmation", "delivery_review", "uat"]
AgentStatus = Literal["active", "planned", "human"]
TaskType = Literal["agent", "human"]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class RequirementItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: str
    statement: str
    priority: Literal["must", "should", "could"] = "must"
    acceptance_criteria: list[str] = Field(default_factory=list)
    source: str = "client brief"


class AgentDefinition(BaseModel):
    """The explicit role contract exposed to the team and the UI."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: str
    stage: str
    status: AgentStatus = "active"
    description: str
    responsibilities: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    input_contract: str
    output_contract: str
    execution_key: str | None = None
    parallel_group: str | None = None
    approval_required: bool = False
    max_retries: int = Field(default=2, ge=0, le=5)


class WorkflowTaskDefinition(BaseModel):
    """A graph task with dependencies rather than an unstructured prompt."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    stage: str
    type: TaskType = "agent"
    agent_id: str
    dependencies: list[str] = Field(default_factory=list)
    parallel_group: str | None = None
    human_gate: str | None = None
    responsibility: str


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(min_length=2, max_length=120)
    client_name: str = Field(default="Client", max_length=120)
    project_type: ProjectType = "software_product"
    brief: str = Field(min_length=20, max_length=30_000)
    goals: list[str] = Field(default_factory=list, max_length=12)
    target_users: list[str] = Field(default_factory=list, max_length=12)
    platforms: list[str] = Field(default_factory=list, max_length=12)
    integrations: list[str] = Field(default_factory=list, max_length=20)
    constraints: list[str] = Field(default_factory=list, max_length=20)
    timeline: str = "To be confirmed"
    budget: str = "To be confirmed"
    brand_preferences: str = ""
    execution_mode: Literal["automatic", "reviewed"] = "reviewed"

    @field_validator("goals", "target_users", "platforms", "integrations", "constraints")
    @classmethod
    def remove_empty_values(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values if value and value.strip()]


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("artifact"))
    kind: ArtifactKind
    title: str
    version: int = 1
    status: Literal["draft", "approved", "final"] = "draft"
    generated_by: list[str] = Field(default_factory=list)
    generated_at: str = Field(default_factory=now_iso)
    content: dict[str, Any]


class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("trace"))
    timestamp: str = Field(default_factory=now_iso)
    stage: str
    agent: str
    status: Literal["started", "completed", "waiting", "failed"]
    message: str
    duration_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class HumanGate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("gate"))
    stage: GateStage
    title: str
    description: str
    questions: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    status: Literal["pending", "resolved"] = "pending"
    decision: str | None = None
    feedback: str = ""
    created_at: str = Field(default_factory=now_iso)
    resolved_at: str | None = None


class NodeExecution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: Literal["pending", "running", "completed", "failed", "waiting", "skipped", "cancelled"] = "pending"
    attempt: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    generation_mode: str = "not_started"


class RunEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int
    timestamp: str = Field(default_factory=now_iso)
    node_id: str
    kind: str
    message: str
    attempt: int = 0
    data: dict[str, Any] = Field(default_factory=dict)


class LiveRun(BaseModel):
    """Persisted in the project snapshot, with CAS ownership across workers."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("run"))
    graph_version: int = 1
    nodes: dict[str, NodeExecution] = Field(default_factory=dict)
    events: list[RunEvent] = Field(default_factory=list)
    event_sequence: int = 0
    lease_owner: str | None = None
    lease_until: float = 0
    # Keep prior attempts available in the node inspector after retry/revision.
    history: list[NodeExecution] = Field(default_factory=list)


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("project"))
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)
    status: ProjectStatus = "requirements_review"
    current_stage: str = "client_requirements"
    input: ProjectInput
    requirements: list[RequirementItem] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    gates: list[HumanGate] = Field(default_factory=list)
    active_gate_id: str | None = None
    decisions: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    revision_feedback: str = ""
    error: str | None = None
    live_run: LiveRun | None = None

    @property
    def active_gate(self) -> HumanGate | None:
        if not self.active_gate_id:
            return None
        return next((gate for gate in self.gates if gate.id == self.active_gate_id), None)


class CreateProjectRequest(ProjectInput):
    """API body for creating a project."""


class GateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "request_changes", "reject", "pass", "fail"]
    feedback: str = Field(default="", max_length=10_000)
    answers: dict[str, str] = Field(default_factory=dict)
    request_brd: bool = False


class ProjectSummary(BaseModel):
    id: str
    project_name: str
    client_name: str
    project_type: ProjectType
    status: ProjectStatus
    current_stage: str
    artifact_count: int
    active_gate: str | None
    updated_at: str
    live: bool = False


def summarize(project: Project) -> ProjectSummary:
    gate = project.active_gate
    return ProjectSummary(
        id=project.id,
        project_name=project.input.project_name,
        client_name=project.input.client_name,
        project_type=project.input.project_type,
        status=project.status,
        current_stage=project.current_stage,
        artifact_count=len(project.artifacts),
        active_gate=gate.title if gate else None,
        updated_at=project.updated_at,
        live=project.live_run is not None,
    )
