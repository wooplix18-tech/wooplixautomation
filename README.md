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
  decisions, and a clearly labelled 500 → 40 + 18 hour planning target.
- **BRD generation** — maps every requirement to priority, acceptance criteria,
  non-functional requirements, and delivery stages.
- **UI/UX design** — generates personas, journeys, screens, states,
  accessibility principles, and handoff contents.
- **Software development planning** — generates architecture, backlog items,
  API contracts, repository structure, and quality gates.
- **Testing and UAT** — creates traceable test cases, release gates, and a UAT
  script that a client can execute.
- **Documentation and training** — creates stakeholder, user, technical, and
  support documentation plans plus a knowledge-transfer checklist.
- **Human review gates** — scope confirmation, delivery pack review, and UAT
  signoff. Changes trigger a bounded revision cycle.
- **Durable persistence** — SQLite stores projects, artifacts, gates,
  decisions, revisions, and trace events.
- **Optional model provider** — set an OpenAI-compatible or Groq API key to
  enable model calls. Without credentials, deterministic demo mode runs
  locally and in CI with the same contracts.
- **Browser cockpit** — a responsive dark dashboard at `/` shows workflow
  progress, metrics, artifacts, decisions, and the agent trace.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/> and create a workspace. The app intentionally
pauses at the scope confirmation gate. Approve it to run the specialist
pipeline, then approve the delivery review and pass UAT to create the final
handover record.

The database is created at `data/automation.db`. Set `AUTOMATION_DB_PATH` to a
different location for a separate workspace or test run.

## Deploy on Vercel

The repository includes `api/index.py` and `vercel.json` so Vercel can detect
the FastAPI serverless function. Import
`wooplix18-tech/wooplixautomation` into Vercel with the repository root as the
project root, then deploy. The Python function serves both the API and the
browser cockpit.

For a first deployment, Vercel uses `/tmp/automation.db` when `VERCEL=1` so
the function can start on its writable filesystem. Vercel `/tmp` storage is
ephemeral and is not a production database. Configure a hosted PostgreSQL or
other durable store before relying on project persistence across serverless
instances; the local SQLite store remains the development implementation.

Recommended environment variables in Vercel:

```text
VERCEL=1
GROQ_API_KEY=<optional>
GROQ_MODEL=llama-3.3-70b-versatile
```

After deployment, verify:

```text
https://YOUR-VERCEL-DOMAIN/api/health
https://YOUR-VERCEL-DOMAIN/
```

## Optional AI provider

Copy `.env.example` to `.env` or export variables in the shell. Either an
OpenAI-compatible provider or Groq can be used:

```bash
export GROQ_API_KEY="..."
export GROQ_MODEL="llama-3.3-70b-versatile"
```

The provider adapter is deliberately conservative: typed deterministic
fallbacks ensure the workflow remains usable when the provider is unavailable,
rate-limited, or returns malformed JSON. Credentials are never persisted in a
project state or returned by the health endpoint.

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Service and provider status |
| `GET /api/workflow` | Workflow node and edge metadata |
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
PYTHONPATH=. .venv/bin/python scripts/demo.py
PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -v
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
- `app/storage.py` — SQLite project store.
- `app/main.py` — FastAPI API and static cockpit entrypoint.
- `app/static/index.html` — zero-build responsive project dashboard.
- `tests/test_workflow.py` — offline lifecycle, revision, persistence, and
  topology checks.

The orchestration state is intentionally compatible with a future LangGraph
checkpointer migration. The `langgraph` integration is an optional dependency
in `pyproject.toml`; the current core uses a small explicit state machine so a
new checkout is runnable before optional graph dependencies are installed.

## Delivery target

The proposal artifact exposes the requested **approximately 500 hours of
traditional work versus 40 hours of AI-assisted work and 15–20 hours of human
involvement** as a planning target, not a universal promise. Each real project
should replace the target with evidence-based scope, integration, and UAT
estimates after the requirements gate.
