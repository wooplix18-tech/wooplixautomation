# Wooplix AI Software Development System

An AI-assisted software delivery workspace that coordinates the complete
software development lifecycle for websites, mobile applications, internal
tools, and other software products.

The system turns one client brief into a source-grounded delivery pack:

```text
Client brief
  → Requirement Analyst + human scope confirmation
  → Proposal Agent ─────────┐
  → BRD Agent ──────────────┤ parallel planning
  → UI/UX Design Agent ─────┤
  → Software Development Agent
  → Testing & UAT Agent
  → Documentation & Training Agent
  → Human delivery review
  → Client UAT and handover
```

This is the first runnable foundation for the `automationlanggraph` product.
It follows the patterns established in the Wooplix proposal agent: typed
artifacts, a stable requirement register, bounded specialist roles, explicit
human checkpoints, durable state, and an auditable workflow trace.

## What is included

- **Client requirements collection** — accepts a structured intake and a free
  text brief, then creates stable `REQ-*` IDs and acceptance criteria.
- **Proposal generation** — creates scope, deliverables, assumptions, open
  decisions grounded in the supplied requirements.
- **BRD generation** — maps every requirement to priority, acceptance criteria,
  non-functional requirements, and delivery stages.
- **UI/UX design** — generates personas, journeys, screens, states,
  accessibility principles, and handoff contents.
- **Software development planning** — generates architecture, backlog items,
  API contracts, repository structure, and quality gates.
- **Testing and UAT** — creates traceable test cases, release gates, and a UAT
  script that a client can execute.
- **Documentation and training** — generates a source-linked user guide,
  proposed technical reference, support triage guide, role-based training
  practice, and handover checklist. It marks implementation details that still
  need verification before release.
- **Conversational intake** — paste one brief, discuss requirements with the
  project assistant, or upload a TXT, Markdown, text-enabled PDF, or DOCX file.
- **Automatic delivery pack** — generate all seven planning deliverables
  without intermediate approval stops. Optional reviewed mode keeps scope,
  delivery review, and UAT checkpoints.
- **Durable persistence** — Neon PostgreSQL or local SQLite stores projects, artifacts, gates,
  decisions, revisions, and trace events.
- **Optional model provider** — set an OpenAI-compatible or Groq API key to
  enable model calls. Without credentials, deterministic demo mode runs
  locally and in CI with the same contracts.
- **Browser cockpit** — a responsive dark dashboard at `/` shows workflow
  progress, metrics, artifacts, decisions, and the agent trace.

## Quick start

### Live Workflow Studio

Open **`/workflow`** for the new execution canvas. New projects created from the
inquiry screen open here immediately, before generation begins. Use **Execute**
to run successive ready waves or **Step** to run one wave. Click any node or
connection to inspect its input references, saved output, attempt history and
events. Pan, zoom, fit and rearrange the canvas without changing dependencies.

The runner saves each node independently with a renewable, database-fenced
lease. The read-only SSE event stream restores saved progress on refresh and
reconnect. Retry keeps completed outputs, cancellation discards late results,
and reviewed mode pauses on real human gates. Automatic planning marks those
gates skipped; it never reports that UAT was performed.

**On Vercel, live runs require `DATABASE_URL`.** Ephemeral SQLite cannot share
state reliably between execution and event requests. Local SQLite remains
supported. No new database table migration is needed for the live-run fields.

To schedule work after the browser closes, run a separate long-lived Python
worker with the same database environment:

```bash
.venv/bin/python -m scripts.worker
```

The worker entrypoint is included; worker hosting is not provisioned by this
change. A browser alone schedules one HTTP wave at a time. Long nested model
graphs can still exceed Vercel's function duration; recover interrupted work
after lease expiry or use the worker on a suitable long-lived host. Nested
LangGraph milestones are visible, but resume currently operates at the parent
document-node boundary.

Architecture guide: [docs/WORKFLOW_ARCHITECTURE.md](docs/WORKFLOW_ARCHITECTURE.md).
Generate its Word version (with three graph diagrams) using:

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m scripts.export_architecture
# out/Wooplix-Workflow-Architecture.docx
```

Browser verification using an isolated database and no model calls:

```bash
.venv/bin/python -m scripts.browser_flow_smoke
```

The canvas executes a fixed delivery graph. Editable graph definitions, custom
connectors, staff authorization and nested durable checkpoints remain roadmap
items, documented in the architecture guide.

Use Python 3.12 or newer (the tested local/Vercel version is pinned to 3.14 in
`.python-version`). From the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env.local  # only if .env.local does not already exist
./scripts/start.sh
```

Open <http://127.0.0.1:8000/>. This checkout already has a configured
`.env.local`; preserve its existing credentials. The application automatically
loads `.env.local`, then `.env`, without overriding variables supplied by the
shell. Vercel uses hosting environment variables and never loads local files.

With `DATABASE_URL` set, projects are persisted in Neon PostgreSQL. Without
it, local development uses `data/automation.db`. To explicitly run an offline
SQLite demo even when `.env.local` contains Neon settings:

```bash
DATABASE_URL='' GROQ_API_KEY='' OPENAI_API_KEY='' ./scripts/start.sh
```

The home screen starts with the project assistant. Paste requirements or
attach one document (up to 2 MB and 30,000 extracted characters). Follow-up
questions are optional: click Generate delivery pack whenever the brief is
complete. Automatic mode generates the seven deliverables and saves the project
as `pack_ready`. Conversation drafts stay in the browser tab until generation.

If you select reviewed mode, the workflow pauses for scope confirmation,
delivery review, and UAT. Failed UAT keeps handover blocked; duplicate and stale
decisions return a conflict. Existing reviewed projects retain their behavior.

Without an AI-provider key, guided intake and deterministic artifact generation
remain available. A configured Groq/OpenAI-compatible provider enables dynamic
chat questions and metadata extraction. Original user requirements are always
preserved even if the model omits them in its summary.

Uploads are processed as text; the original files are not stored. PDF scans
without a text layer need OCR before upload. Word tables and paragraphs are
included in the extracted requirements.

## Deploy on Vercel

See [DEPLOYMENT.md](DEPLOYMENT.md) for the exact account, root directory,
environment import, and verification steps.

Import `wooplix18-tech/wooplixautomation` into the intended Vercel account.
**Use the repository root (`.`), not `app/`, as Root Directory.** Select the
FastAPI preset. The modern Python entrypoint is `app.main:app`, defined in
`pyproject.toml`; `vercel.json` sets a 300-second function limit. Leave Build,
Install, and Output settings at the FastAPI defaults. The cockpit HTML is
served by the Python application and requires no frontend build.

Import the private `.env.vercel` file in Vercel's Environment Variables screen
before deployment. `DATABASE_URL` is required for durable hosted storage;
use this project's pooled Neon connection. AI keys are optional. Vercel sets
`VERCEL=1` automatically. The existing `api/index.py` remains a compatibility
wrapper; the native FastAPI deployment uses `app/main.py`.

If `DATABASE_URL` is absent, the hosted app falls back to `/tmp/automation.db`
and the health response reports `durable_storage: false`. This is only suitable
for disposable demos: projects can disappear across invocations. Do not set
`AUTOMATION_DB_PATH=data/automation.db` on Vercel, whose deployment filesystem
is read-only.

The existing `neon.ts` and `hello.ts` are optional Neon infrastructure examples.
They do not provide authentication for this FastAPI app and are not required
for its Vercel deployment. All API callers currently share one workspace;
use Vercel deployment protection for a private workspace until application
login and authorization are implemented.

## Optional AI provider

The proposal and BRD stages now integrate the generation core from
`wooplix15-debug/proposalagent`, pinned to commit
`0ab21f9c70ced79c93bed2b18f1f71d6831c259a`. Its supervisor, parallel specialists,
writer, source validation, quality critic and bounded repair run inside the
project workflow. With `PROPOSAL_ENGINE=auto` and a Groq key, these stages use
the integrated graph; without the key they use templates. Set
`PROPOSAL_ENGINE=langgraph` to require live Groq generation explicitly.

Integrated outputs retain their source links, quality reports and nested graph
trace. The proposal and BRD supply context to downstream agents. Artifact cards
offer PDF and Word downloads from saved content. PDFs use Dompdf through
`api/render-pdf.php`. `/api/proposal-agent/workflow` exposes the actual nested
graph topology without invoking a model.

See [AI_ENGINEERING_REVIEW.md](AI_ENGINEERING_REVIEW.md) for findings and the
remaining reliability/access-control work. This integration covers the source
generation core and document exports. Its separate Telegram delivery and
persistent standalone case API are not enabled in this workspace.

For local PDF rendering, install PHP 8.1+ and Composer, then run
`composer install`. Vercel builds the PHP function with the configured
`vercel-php` runtime; FastAPI calls that renderer when a user downloads a PDF.

Set either `GROQ_API_KEY` with `GROQ_MODEL`, or `OPENAI_API_KEY` with `AI_MODEL`,
in `.env.local` and Vercel settings. Groq takes precedence if both keys exist.
Without a key, the deterministic planning workflow works offline.

Provider responses are optional enhancements. Unavailable providers or malformed
optional JSON fields fall back to deterministic artifacts. Revision requests
are included in provider prompts and recorded in revised artifacts; they do
not execute application code or guarantee that the requested feature has been
implemented.

The lightweight provider adapter allows 45 seconds per call; the imported
document graph has its own retries and potentially longer overall duration.
Legacy projects generate synchronously in their review request. New live runs
execute one ready wave per request or worker cycle and persist each node result.
If a request times out, inspect the saved run before retrying. Expired leases can
be recovered, but nested document calls still need finer-grained checkpoints
and a long-lived worker for reliable unattended operation.

The store creates `automation_projects` and its timestamp index on first use,
not during entrypoint import/build. Snapshot saves use an optimistic timestamp
check to prevent one instance from overwriting another's review decision.
`GET /api/health` checks connectivity and reports the backend without exposing
credentials. It does not verify an external model call.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service and provider status |
| `GET /api/workflow` | Workflow node and edge metadata |
| `GET /api/agents` | Agent roles, responsibilities, contracts, and execution status |
| `POST /api/runs` | Queue a live run without calling a model |
| `GET /api/live/topology` | Fixed execution graph including scope review |
| `GET /api/projects/{id}/flow` | Compact execution state and incremental events |
| `GET /api/projects/{id}/events` | SSE progress and replay via Last-Event-ID |
| `GET /api/projects/{id}/nodes/{node}` | Node input/output and prior attempts |
| `POST /api/projects/{id}/execute` | Execute one ready parallel wave |
| `POST /api/projects/{id}/retry` | Recover failed/interrupted nodes |
| `POST /api/projects/{id}/cancel` | Cancel scheduling and discard late outputs |
| `POST /api/intake/chat` | Ask an inquiry question and organize the conversation into a project draft |
| `POST /api/intake/document` | Extract requirements from one uploaded document |
| `POST /api/projects` | Create a project and extract requirements |
| `GET /api/projects` | List project summaries |
| `GET /api/projects/{id}` | Restore complete durable project state |
| `POST /api/projects/{id}/gates/{gate_id}/respond` | Approve, revise, reject, or pass a human gate |
| `GET /api/projects/{id}/artifacts/{kind}` | Read one generated artifact |

Example project request:

```json
{
  "project_name": "Partner onboarding portal",
  "client_name": "Acme Services",
  "project_type": "website",
  "brief": "Create a responsive portal where partners register, submit onboarding information, track approval status, and receive notifications.",
  "goals": ["Reduce manual onboarding effort"],
  "target_users": ["Partners", "Operations team"],
  "platforms": ["Web"],
  "integrations": ["Email provider"]
}
```

Example gate response:

```json
{
  "decision": "approve",
  "feedback": "Scope confirmed by the client."
}
```

## Run the demo and tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
GROQ_API_KEY='' OPENAI_API_KEY='' .venv/bin/python -m scripts.demo
.venv/bin/python -m unittest discover -s tests -v
```

The system does not execute generated application code. The development agent
produces an implementation blueprint and starter backlog for human-approved
engineering work. This boundary keeps client review, repository access, and
production deployment under explicit control while still automating the
highest-volume planning and documentation work.

## Architecture

- `app/models.py` — Pydantic contracts for projects, requirements, artifacts,
  gates, and trace events.
- `app/agents.py` — specialist agents and optional OpenAI-compatible adapter.
- `app/workflow.py` — durable orchestration, parallel specialist execution,
  revision routing, and human checkpoints.
- `app/storage.py` — Neon PostgreSQL store with SQLite local fallback.
- `app/registry.py` — explicit agent role contracts and dependency-aware task graph.
- `app/main.py` — FastAPI API and static cockpit entrypoint.
- `app/intake.py` — conversational intake, bounded PDF/Word/text extraction,
  and project metadata collection.
- `app/static/index.html` — zero-build responsive project dashboard.
- `tests/test_workflow.py` — offline lifecycle, revision, persistence, and
  topology checks.

The top-level live runner uses an explicit dependency graph and persisted
snapshot checkpoints. Proposal/BRD nodes use the imported LangGraph subgraphs
when Groq is configured. LangGraph is an installed runtime dependency; nested
PostgreSQL checkpointers are a separate future step.
