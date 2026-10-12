"""Checkpointed execution waves, shared by the HTTP runner and worker CLI.

No fire-and-forget serverless tasks: an execution request owns one ready wave.
Each node is committed independently and fenced with a renewable lease. Events
and node checkpoints live in the same atomic project snapshot in either store.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

from .models import LiveRun, NodeExecution, Project, RunEvent, new_id, now_iso
from .progress import observe
from .registry import WORKFLOW_TASKS
from .storage import StorageConflict
from .workflow import WorkflowError

LEASE_SECONDS = 360
MAX_ATTEMPTS = 3
EVENT_RETENTION = 1000


def live_topology(mode: str = "reviewed") -> dict:
    nodes = [row.model_dump(mode="json") for row in WORKFLOW_TASKS]
    friendly = {
        "client_requirements": ("Understand your brief", "The AI reads your requirements and identifies the requested work."),
        "proposal": ("Prepare your proposal", "The AI drafts scope, delivery phases, costs to confirm, and open questions."),
        "brd": ("Write the BRD", "The AI turns the approved proposal into detailed business requirements."),
        "ui_ux": ("Plan the user experience", "The AI outlines user journeys, screens, and interactions."),
        "implementation": ("Plan how to build it", "The AI prepares a technical approach and work plan."),
        "qa_uat": ("Check the requirements", "The AI prepares acceptance checks and test cases."),
        "documentation": ("Prepare guides and training", "The AI creates user, technical, and support material."),
        "delivery_review": ("Review the delivery", "Your team checks the completed documents."),
        "uat": ("Client acceptance", "The client records whether the agreed work is acceptable."),
        "handover": ("Prepare handover", "The AI organizes final documents and ownership details."),
    }
    for node in nodes:
        if node["id"] in friendly:
            node["label"], node["responsibility"] = friendly[node["id"]]
    for node in nodes:
        node["type"] = "human" if node["id"] in {"delivery_review", "uat"} else "agent"
        node["skippable"] = mode == "automatic" and node["type"] == "human"
    return {"version": 1, "nodes": nodes,
            "edges": [[parent, node["id"]] for node in nodes for parent in node["dependencies"]]}


TASKS = {node["id"]: node for node in live_topology()["nodes"]}
KEYS = {"client_requirements": "requirements", **{k: k for k in TASKS if k not in {"client_requirements", "delivery_review", "uat"}}}
GATES = {"client_requirements": "requirements_confirmation", "delivery_review": "delivery_review", "uat": "uat"}


class LeaseLost(WorkflowError):
    pass


def record(project: Project, node_id: str, kind: str, message: str, **data) -> None:
    run = project.live_run
    run.event_sequence += 1
    node = run.nodes.get(node_id)
    run.events.append(RunEvent(sequence=run.event_sequence, node_id=node_id, kind=kind,
                               message=message, attempt=node.attempt if node else 0, data=data))
    run.events = run.events[-EVENT_RETENTION:]


def summary(project: Project, after: int = 0) -> dict:
    run = project.live_run
    if not run:
        return {"id": project.id, "legacy": True, "status": project.status}
    return {
        "id": project.id, "run_id": run.id, "legacy": False,
        "name": project.input.project_name, "status": project.status,
        "mode": project.input.execution_mode, "updated_at": project.updated_at,
        "nodes": {key: node.model_dump(exclude={"input_data", "output_data"}) for key, node in run.nodes.items()},
        "events": [e.model_dump(mode="json") for e in run.events if e.sequence > after],
        "cursor": run.event_sequence,
        "history_truncated": bool(run.events and after and after < run.events[0].sequence - 1),
        "lease_active": bool(run.lease_owner and run.lease_until > time.time()),
        "recoverable": bool(run.lease_owner and run.lease_until <= time.time()),
        "retryable": not any(n.output_data.get("decision") == "reject" for n in run.nodes.values())
                     and any(n.status in {"failed", "cancelled", "running"} for n in run.nodes.values()),
        "active_gate": project.active_gate.model_dump(mode="json") if project.active_gate else None,
        "artifacts": [a.model_dump(exclude={"content"}) for a in project.artifacts],
    }


class LiveRunner:
    def __init__(self, engine):
        self.engine = engine
        self.store = engine.store

    def create(self, project_input) -> Project:
        project = Project(input=project_input, status="queued",
                          live_run=LiveRun(nodes={key: NodeExecution(id=key) for key in TASKS}))
        record(project, "workflow", "queued", "Run saved. Ready to execute the requirement analyst.")
        return self.store.save(project)

    def _change(self, project_id, operation):
        # Reload before each mutation: parallel results and control actions merge
        # through the database CAS, not through shared mutable Project objects.
        for attempt in range(10):
            project = self.engine.get(project_id)
            if not project.live_run:
                raise WorkflowError("This project uses the legacy runner. Start a new live run.")
            operation(project)
            try:
                return self.store.save(project)
            except StorageConflict:
                if attempt == 9:
                    raise
                time.sleep(0.01 * (attempt + 1))

    @staticmethod
    def _owned(project, owner):
        run = project.live_run
        if run.lease_owner != owner or run.lease_until <= time.time() or project.status == "cancelled":
            raise LeaseLost("Execution ownership expired or the run was cancelled.")

    def _settle(self, project):
        run = project.live_run
        requirements = run.nodes["client_requirements"]
        if (requirements.status == "completed" and project.input.execution_mode != "automatic"
                and not project.active_gate_id
                and not any(decision.get("stage") == "requirements_confirmation"
                            for decision in project.decisions)):
            task = TASKS["client_requirements"]
            self.engine._open_gate(project, GATES["client_requirements"], task["label"],
                                   task["responsibility"], project.metrics.get("open_questions", []),
                                   {"node_id": "client_requirements",
                                    "requirements": [r.model_dump(mode="json") for r in project.requirements]})
            record(project, "client_requirements", "waiting", "Waiting for a human decision.")
            return
        for key, task in TASKS.items():
            node = run.nodes[key]
            if node.status != "pending" or task["type"] != "human":
                continue
            if not all(run.nodes[parent].status in {"completed", "skipped"} for parent in task["dependencies"]):
                continue
            if project.input.execution_mode == "automatic":
                node.status = "skipped"
                node.finished_at = now_iso()
                node.output_data = {"reason": "Planning-only automatic mode; no human approval or UAT was performed."}
                record(project, key, "skipped", node.output_data["reason"])
                continue
            self.engine._open_gate(project, GATES[key], task["label"], task["responsibility"],
                                   project.metrics.get("open_questions", []) if key == "client_requirements" else [],
                                   {"node_id": key})
            node.status = "waiting"
            node.input_data = {"requirements": [r.model_dump() for r in project.requirements],
                               "artifact_ids": [a.id for a in project.artifacts]}
            record(project, key, "waiting", "Waiting for a human decision.")
        if all(n.status in {"completed", "skipped"} for n in run.nodes.values()):
            project.status = "pack_ready" if project.input.execution_mode == "automatic" else "completed"
            project.current_stage = "handover"
        elif any(n.status == "failed" for n in run.nodes.values()):
            project.status = "failed"
        elif project.active_gate_id:
            return
        else:
            project.status = "queued"

    def _claim(self, project_id, owner):
        def claim(project):
            run = project.live_run
            if run.lease_owner:
                if run.lease_until > time.time():
                    raise WorkflowError("Another execution request owns this wave. Watch its progress.")
                raise WorkflowError("The previous execution was interrupted. Use Retry to recover unfinished nodes.")
            if project.status in {"failed", "cancelled", "pack_ready", "completed"} or project.active_gate_id:
                raise WorkflowError("The run is stopped or waiting for review.")
            self._settle(project)
            if project.active_gate_id:
                raise WorkflowError("This run is waiting for human review.")
            ready = [key for key, task in TASKS.items() if task["type"] == "agent"
                     and run.nodes[key].status == "pending"
                     and all(run.nodes[p].status in {"completed", "skipped"} for p in task["dependencies"])]
            if not ready:
                raise WorkflowError("No executable nodes are ready.")
            run.lease_owner = owner
            run.lease_until = time.time() + LEASE_SECONDS
            project.status = "running"
            project.error = None
            project.current_stage = ready[0]
            for key in ready:
                node = run.nodes[key]
                node.status = "running"
                node.attempt += 1
                node.started_at = now_iso()
                node.finished_at = None
                node.error = None
                node.generation_mode = "executing"
                # Captured input references point at immutable node attempts.
                node.input_data = {"project": project.input.model_dump(mode="json"),
                                   "requirement_ids": [r.id for r in project.requirements],
                                   "upstream": [{"node_id": p, "attempt": run.nodes[p].attempt}
                                                for p in TASKS[key]["dependencies"]],
                                   "artifact_versions": [{"id": a.id, "kind": a.kind, "version": a.version}
                                                         for a in project.artifacts],
                                   "revision_feedback": project.revision_feedback}
                record(project, key, "started", f"{TASKS[key]['label']} started.")
        return self._change(project_id, claim)

    def _progress(self, project_id, owner, key, kind, message, data):
        def update(project):
            self._owned(project, owner)
            node = project.live_run.nodes[key]
            if kind in {"model_completed", "template", "fallback"}:
                node.generation_mode = {"model_completed": "model", "template": "template", "fallback": "fallback"}[kind]
            record(project, key, kind, message, **data)
        self._change(project_id, update)

    def _call(self, snapshot, owner, key):
        # Each parallel agent receives its own immutable snapshot from the wave
        # boundary. A sibling cannot leak partial output into another's inputs.
        view = snapshot.model_copy(deep=True)
        view.live_run = None
        started = time.perf_counter()
        with observe(lambda kind, message, data: self._progress(snapshot.id, owner, key, kind, message, data)):
            agent = self.engine.agents[KEYS[key]]
            output = agent.run(view.input if key == "client_requirements" else view)
        return output, round((time.perf_counter() - started) * 1000)

    def _complete(self, project_id, owner, key, output, duration):
        def complete(project):
            self._owned(project, owner)
            node = project.live_run.nodes[key]
            if key == "client_requirements":
                project.requirements = output["requirements"]
                project.metrics.update(requirement_count=len(project.requirements), open_questions=output["questions"])
                for field in ("proposal_context", "analysis_trace"):
                    if field in output:
                        project.metrics[field] = output[field]
                node.output_data = {"requirements": [r.model_dump(mode="json") for r in project.requirements],
                                    "questions": output["questions"]}
            else:
                if project.revision_feedback:
                    output.content["revision_feedback"] = project.revision_feedback
                previous = [n.output_data for n in project.live_run.history if n.id == key and n.output_data.get("kind") == output.kind]
                if previous:
                    latest = max(previous, key=lambda a: a["version"])
                    output.id, output.version = latest["id"], latest["version"] + 1
                self.engine._artifact(project, output)
                node.output_data = output.model_dump(mode="json")
            node.status = "completed"
            node.duration_ms = duration
            node.finished_at = now_iso()
            if node.generation_mode == "executing":
                node.generation_mode = "deterministic" if key == "handover" else "agent"
            self.engine._trace(project, key, TASKS[key]["agent_id"], "completed", f"{TASKS[key]['label']} checkpoint saved.")
            record(project, key, "completed", "Output checkpoint saved.", duration_ms=duration)
        self._change(project_id, complete)

    def execute_wave(self, project_id: str) -> Project:
        owner = new_id("lease")
        snapshot = self._claim(project_id, owner)
        ready = [key for key, node in snapshot.live_run.nodes.items() if node.status == "running"]
        # Remains inside the request/worker lifetime. Nothing depends on an ASGI
        # background thread continuing after a serverless response has ended.
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="live-node") as pool:
            pending = {pool.submit(self._call, snapshot, owner, key): key for key in ready}
            heartbeat = time.monotonic()
            while pending:
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                if time.monotonic() - heartbeat >= 10:
                    def renew(project):
                        self._owned(project, owner)
                        project.live_run.lease_until = time.time() + LEASE_SECONDS
                    try:
                        self._change(project_id, renew)
                    except LeaseLost:
                        pass  # in-flight calls finish, but their outputs are fenced out
                    heartbeat = time.monotonic()
                for future in done:
                    key = pending.pop(future)
                    try:
                        output, duration = future.result()
                    except LeaseLost:
                        continue
                    except Exception as exc:
                        # Do not expose arbitrary SDK/driver exception strings.
                        error = f"{TASKS[key]['label']} failed ({type(exc).__name__}). Check provider configuration or retry this node."
                        def fail(project):
                            self._owned(project, owner)
                            node = project.live_run.nodes[key]
                            node.status, node.error, node.finished_at = "failed", error, now_iso()
                            project.error = error
                            record(project, key, "failed", error)
                        try:
                            self._change(project_id, fail)
                        except LeaseLost:
                            pass
                    else:
                        try:
                            self._complete(project_id, owner, key, output, duration)
                        except LeaseLost:
                            pass
        def finish(project):
            self._owned(project, owner)
            project.live_run.lease_owner, project.live_run.lease_until = None, 0
            self._settle(project)
            record(project, "workflow", "wave_finished", f"Wave finished: {project.status}.")
        try:
            return self._change(project_id, finish)
        except LeaseLost:
            return self.engine.get(project_id)

    def cancel(self, project_id):
        def cancel(project):
            if project.status in {"completed", "pack_ready", "cancelled"}:
                raise WorkflowError("The run has already stopped.")
            project.status = "cancelled"
            project.live_run.lease_owner, project.live_run.lease_until = None, 0
            for node in project.live_run.nodes.values():
                if node.status in {"running", "pending", "waiting"}:
                    node.status = "cancelled"
                    node.finished_at = now_iso()
            if project.active_gate:
                project.active_gate.status = "resolved"
                project.active_gate.decision = "cancel"
                project.active_gate.resolved_at = now_iso()
            project.active_gate_id = None
            record(project, "workflow", "cancelled", "Stopped scheduling. In-flight provider calls may finish; their results will be discarded.")
        return self._change(project_id, cancel)

    def retry(self, project_id):
        def retry(project):
            run = project.live_run
            if any(n.output_data.get("decision") == "reject" for n in run.nodes.values()):
                raise WorkflowError("This run was rejected by a reviewer. Create a new run for a revised scope.")
            if run.lease_owner and run.lease_until > time.time():
                raise WorkflowError("A wave is still running. Wait or cancel it first.")
            candidates = [n for n in run.nodes.values() if n.status in {"failed", "cancelled", "running"}]
            if not candidates:
                raise WorkflowError("There are no failed or interrupted nodes to retry.")
            if any(n.attempt >= MAX_ATTEMPTS for n in candidates):
                raise WorkflowError("The three-attempt limit has been reached. Start a new run with corrected inputs.")
            run.lease_owner, run.lease_until = None, 0
            for node in candidates:
                run.history.append(node.model_copy(deep=True))
                node.status = "pending"
                node.error = None
                node.output_data = {}
                record(project, node.id, "retry_queued", "Retry queued; completed nodes are preserved.")
            project.error = None
            self._settle(project)
        return self._change(project_id, retry)

    def respond(self, project_id, gate_id, response):
        def respond(project):
            gate = project.active_gate
            if not gate or gate.id != gate_id or gate.status != "pending":
                raise WorkflowError("This human gate is no longer active.")
            allowed = {"approve", "request_changes", "reject"} | ({"pass", "fail"} if gate.stage == "uat" else set())
            if response.decision not in allowed:
                raise WorkflowError("That decision is not valid for this gate.")
            key = next(key for key, stage in GATES.items() if stage == gate.stage)
            gate.status, gate.decision, gate.feedback, gate.resolved_at = "resolved", response.decision, response.feedback, now_iso()
            project.active_gate_id = None
            decision = {"gate_id": gate.id, "stage": gate.stage, **response.model_dump(), "timestamp": now_iso()}
            project.decisions.append(decision)
            node = project.live_run.nodes[key]
            node.output_data = decision
            node.status, node.finished_at = "completed", now_iso()
            record(project, key, "decision", f"Human decision: {response.decision}.")
            if response.decision == "reject":
                project.status = "cancelled"
                for remaining in project.live_run.nodes.values():
                    if remaining.status == "pending":
                        remaining.status = "cancelled"
                return
            if key == "uat" and response.decision in {"fail", "request_changes"}:
                # A failed UAT never executes handover or marks the gate passed.
                node.status = "pending"
                self._settle(project)
                project.status = "uat_failed"
                return
            if response.decision == "request_changes":
                if not response.feedback.strip():
                    raise WorkflowError("Describe the requested changes before submitting a revision.")
                project.revision_feedback = response.feedback
                first = "client_requirements" if key == "client_requirements" else "proposal"
                if first == "client_requirements":
                    project.input.brief += "\nClient clarification: " + response.feedback
                reset = set()
                for task_id, task in TASKS.items():
                    if task_id == first or (first == "proposal" and task_id == "brd") or any(p in reset for p in task["dependencies"]):
                        reset.add(task_id)
                for task_id in reset:
                    old = project.live_run.nodes[task_id]
                    project.live_run.history.append(old.model_copy(deep=True))
                    project.live_run.nodes[task_id] = NodeExecution(id=task_id, attempt=old.attempt)
                # Retain previous artifacts only in history, so downstream agents
                # cannot accidentally consume a superseded branch of the graph.
                project.artifacts = [a for a in project.artifacts if a.kind not in reset]
            self._settle(project)
        return self._change(project_id, respond)

    def request_brd(self, project_id):
        def request(project):
            node = project.live_run.nodes["brd"]
            if node.status in {"completed", "running"}:
                raise WorkflowError("The BRD has already started or is complete.")
            node.status = "pending"
            node.output_data = {}
            node.finished_at = None
            project.status = "queued"
            record(project, "brd", "requested", "BRD queued for the planning wave.")
        return self._change(project_id, request)

    def inspect_node(self, project_id, node_id):
        project = self.engine.get(project_id)
        run = project.live_run
        if not run or node_id not in run.nodes:
            raise WorkflowError("This execution node does not exist.")
        return {"definition": TASKS[node_id], "execution": run.nodes[node_id].model_dump(mode="json"),
                "history": [n.model_dump(mode="json") for n in run.history if n.id == node_id],
                "events": [e.model_dump(mode="json") for e in run.events if e.node_id == node_id]}
