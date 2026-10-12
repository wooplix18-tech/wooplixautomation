from __future__ import annotations

import os
import asyncio
import json
import time
from pathlib import Path

import psycopg
from fastapi import FastAPI, HTTPException, UploadFile, Request, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .agents import AIClient
from .config import load_environment
from .models import CreateProjectRequest, GateResponse, Project, summarize
from .intake import DocumentError, IntakeReply, IntakeRequest, MAX_UPLOAD_BYTES, extract_document, respond_to_intake
from .registry import AGENT_CATALOG, agent_catalog
from .proposal_integration import proposal_engine
from .exports import artifact_docx, artifact_pdf
from .storage import StorageConflict, build_store
from .workflow import ProjectNotFound, WorkflowEngine, WorkflowError, workflow_topology
from .live import LiveRunner, live_topology, summary as live_summary


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
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "static"), name="assets")
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


@app.exception_handler(WorkflowError)
def workflow_error(_request, exc) -> JSONResponse:
    return JSONResponse(status_code=404 if isinstance(exc, ProjectNotFound) else 409, content={"detail": str(exc)})


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
    return FileResponse(Path(__file__).parent / "static" / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/workflow", include_in_schema=False)
def flow_page() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "flow.html", headers={"Cache-Control": "no-store"})


def _require_live(project_id):
    project = _project_or_404(project_id)
    if not project.live_run:
        raise HTTPException(409, "Legacy project: start a new project to use checkpointed live execution.")
    return project


@app.get("/api/live/topology")
def live_graph(mode: str = "reviewed"):
    return live_topology(mode)


@app.post("/api/runs", status_code=201)
def create_run(request: CreateProjectRequest):
    # A cross-request live run needs a shared store on serverless hosts. An
    # ephemeral /tmp snapshot could otherwise disappear before /execute arrives.
    if os.getenv("VERCEL") == "1" and store.backend != "neon_postgres":
        raise HTTPException(503, "Live runs on Vercel require DATABASE_URL. Configure Neon and redeploy.")
    return live_summary(LiveRunner(engine).create(request))


@app.get("/api/projects/{project_id}/flow")
def flow_state(project_id: str, after: int = Query(0, ge=0)):
    return live_summary(_require_live(project_id), after)


@app.get("/api/projects/{project_id}/nodes/{node_id}")
def inspect_node(project_id: str, node_id: str):
    return LiveRunner(engine).inspect_node(project_id, node_id)


@app.post("/api/projects/{project_id}/execute")
def execute_wave(project_id: str):
    return live_summary(LiveRunner(engine).execute_wave(project_id))


@app.post("/api/projects/{project_id}/request-brd")
def request_brd(project_id: str):
    return live_summary(LiveRunner(engine).request_brd(project_id))


@app.post("/api/projects/{project_id}/retry")
def retry_run(project_id: str):
    return live_summary(LiveRunner(engine).retry(project_id))


@app.post("/api/projects/{project_id}/cancel")
def cancel_run(project_id: str):
    return live_summary(LiveRunner(engine).cancel(project_id))


@app.get("/api/projects/{project_id}/events")
async def run_events(project_id: str, request: Request, after: int = Query(0, ge=0)):
    project = await run_in_threadpool(_require_live, project_id)
    try:
        cursor = max(after, int(request.headers.get("last-event-id", "0")))
    except ValueError:
        raise HTTPException(422, "Invalid event cursor")

    async def stream():
        nonlocal project, cursor
        # Short read-only streams reconnect with Last-Event-ID before a Vercel
        # request limit. They never start work, so reconnecting cannot duplicate it.
        until = time.monotonic() + 25
        while True:
            data = live_summary(project, cursor)
            cursor = data["cursor"]
            yield f"id: {cursor}\nevent: snapshot\ndata: {json.dumps(data)}\n\n"
            if project.status in {"completed", "pack_ready", "cancelled", "failed"} or project.active_gate_id:
                yield "event: idle\ndata: {}\n\n"
                return
            while time.monotonic() < until:
                if await request.is_disconnected():
                    return
                await asyncio.sleep(1)
                try:
                    changed = await run_in_threadpool(store.get_if_changed, project_id, project.updated_at)
                except Exception:
                    yield 'event: unavailable\ndata: {"message":"Progress connection interrupted. Reconnecting…"}\n\n'
                    return
                if changed:
                    project = changed
                    break
                # Heartbeats include lease state so an expired worker becomes
                # recoverable even when no newer database write exists.
                yield f"event: heartbeat\ndata: {json.dumps({'recoverable': bool(project.live_run.lease_owner and project.live_run.lease_until <= time.time())})}\n\n"
            else:
                return

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
    })


@app.get("/api/projects/{project_id}/artifacts/{kind}/download")
def download_artifact(project_id: str, kind: str, format: str = Query("docx", pattern="^(docx|pdf)$")) -> Response:
    project = _project_or_404(project_id)
    artifact = next((row for row in project.artifacts if row.kind == kind), None)
    if artifact is None:
        raise HTTPException(404, "Artifact is not available yet.")
    if format == "pdf":
        content, media_type, extension = artifact_pdf(artifact), "application/pdf", "pdf"
    else:
        content, media_type, extension = artifact_docx(artifact), "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"
    return Response(content, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="wooplix-{artifact.kind}-v{artifact.version}.{extension}"'})


def run() -> None:
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)


if __name__ == "__main__":
    run()
