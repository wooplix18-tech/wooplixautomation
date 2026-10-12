from __future__ import annotations

import os
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response

from .agents import AIClient
from .config import load_environment
from .models import CreateProjectRequest, GateResponse, Project, summarize
from .intake import DocumentError, IntakeReply, IntakeRequest, MAX_UPLOAD_BYTES, extract_document, respond_to_intake
from .registry import AGENT_CATALOG, agent_catalog
from .proposal_integration import proposal_engine
from .exports import artifact_docx
from .storage import StorageConflict, build_store
from .workflow import ProjectNotFound, WorkflowEngine, WorkflowError, workflow_topology


# Vercel functions have a read-only deployment bundle. /tmp is writable, but
# ephemeral; configure a hosted database before using multiple production
# instances. Local development keeps the durable SQLite default.
load_environment()
DB_PATH = os.getenv("AUTOMATION_DB_PATH") or (
    "/tmp/automation.db" if os.getenv("VERCEL") == "1" else "data/automation.db"
)
store = build_store(DB_PATH)
engine = WorkflowEngine(store)
app = FastAPI(title="Wooplix AI SDLC", version="0.1.0", description="Multi-agent software delivery orchestration")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(StorageConflict)
def storage_conflict(_request, _exc) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": "This project changed during your request. Refresh it before responding again."})


@app.exception_handler(psycopg.Error)
def database_error(_request, _exc) -> JSONResponse:
    # Driver exceptions can contain connection details. Keep them off the API.
    return JSONResponse(status_code=503, content={"detail": "Database unavailable. Check DATABASE_URL and retry."})


def _project_or_404(project_id: str) -> Project:
    try:
        return engine.get(project_id)
    except WorkflowError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/health")
def health() -> dict[str, object]:
    store.check_connection()
    return {
        "ok": True,
        "service": "wooplix-ai-sdlc",
        "version": app.version,
        "ai_provider_configured": AIClient().enabled,
        "ai_model": AIClient().model,
        "storage_backend": store.backend,
        "durable_storage": store.backend == "neon_postgres" or os.getenv("VERCEL") != "1",
        "agent_count": len(AGENT_CATALOG),
        "proposal_engine": proposal_engine(),
        "workflow": "requirements → proposal + BRD → UI/UX + development → QA/UAT → documentation → human handover",
    }


@app.get("/api/workflow")
def workflow() -> dict[str, object]:
    return workflow_topology()


@app.get("/api/agents")
def agents() -> dict[str, object]:
    client = AIClient()
    return {"agents": agent_catalog(), "ai_provider_configured": client.enabled, "ai_model": client.model}


@app.get("/api/proposal-agent/workflow")
def proposal_agent_workflow() -> dict[str, object]:
    from .proposal_agent.multi_agent_graph import build_graph
    return {"engine": "proposalagent-langgraph", "configured": proposal_engine() == "langgraph",
            "source_commit": "0ab21f9c70ced79c93bed2b18f1f71d6831c259a",
            "graph": build_graph().get_graph().to_json()}


@app.post("/api/intake/chat", response_model=IntakeReply)
def intake_chat(request: IntakeRequest) -> IntakeReply:
    try:
        return respond_to_intake(request)
    except DocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.post("/api/intake/document")
def intake_document(file: UploadFile) -> dict[str, object]:
    try:
        content = file.file.read(MAX_UPLOAD_BYTES + 1)
        text = extract_document(file.filename or "document", content)
        return {"filename": Path(file.filename or "document").name, "text": text, "characters": len(text)}
    except DocumentError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    finally:
        file.file.close()


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
    except ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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


@app.get("/api/projects/{project_id}/artifacts/{kind}/download")
def download_artifact(project_id: str, kind: str) -> Response:
    project = _project_or_404(project_id)
    artifact = next((row for row in project.artifacts if row.kind == kind), None)
    if artifact is None:
        raise HTTPException(404, "Artifact is not available yet.")
    return Response(artifact_docx(artifact), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="wooplix-{artifact.kind}-v{artifact.version}.docx"'})


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)


if __name__ == "__main__":
    run()
