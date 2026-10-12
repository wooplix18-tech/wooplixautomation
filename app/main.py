from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .agents import AIClient
from .models import CreateProjectRequest, GateResponse, Project, summarize
from .storage import ProjectStore
from .workflow import WorkflowEngine, WorkflowError, workflow_topology


DB_PATH = os.getenv("AUTOMATION_DB_PATH", "data/automation.db")
store = ProjectStore(DB_PATH)
engine = WorkflowEngine(store)
app = FastAPI(title="Wooplix AI SDLC", version="0.1.0", description="Multi-agent software delivery orchestration")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _project_or_404(project_id: str) -> Project:
    try:
        return engine.get(project_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "wooplix-ai-sdlc",
        "version": app.version,
        "ai_provider_configured": AIClient().enabled,
        "workflow": "requirements → proposal + BRD → UI/UX + development → QA/UAT → documentation → human handover",
    }


@app.get("/api/workflow")
def workflow() -> dict[str, object]:
    return workflow_topology()


@app.post("/api/projects", response_model=Project, status_code=201)
def create_project(request: CreateProjectRequest) -> Project:
    return engine.create(request)


@app.get("/api/projects")
def list_projects() -> dict[str, object]:
    projects = engine.list()
    return {"projects": [summarize(project).model_dump(mode="json") for project in projects]}


@app.get("/api/projects/{project_id}", response_model=Project)
def get_project(project_id: str) -> Project:
    return _project_or_404(project_id)


@app.post("/api/projects/{project_id}/run", response_model=Project)
def run_project(project_id: str) -> Project:
    # Creation intentionally stops at a requirements gate. This endpoint is a
    # safe resume helper for integrations that have already recorded approval.
    project = _project_or_404(project_id)
    if project.active_gate_id:
        raise HTTPException(status_code=409, detail="Resolve the active human gate before running the next stage.")
    return project


@app.post("/api/projects/{project_id}/gates/{gate_id}/respond", response_model=Project)
def respond_to_gate(project_id: str, gate_id: str, response: GateResponse) -> Project:
    try:
        return engine.respond(project_id, gate_id, response)
    except WorkflowError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/projects/{project_id}/artifacts/{kind}")
def get_artifact(project_id: str, kind: str) -> dict[str, object]:
    project = _project_or_404(project_id)
    artifact = next((row for row in project.artifacts if row.kind == kind), None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact {kind} is not available yet.")
    return artifact.model_dump(mode="json")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
