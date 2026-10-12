# Wooplix AI system: engineering review and integration

Reviewed on 2026-10-12. Proposal-agent reference commit:
`0ab21f9c70ced79c93bed2b18f1f71d6831c259a` from
[wooplix15-debug/proposalagent](https://github.com/wooplix15-debug/proposalagent).

## Assessment

The new system is a useful shared intake and planning workspace. The existing
proposal agent has stronger document-generation controls: actual specialist
model calls, typed findings, source IDs, reconciliation, independent critique,
restricted repair, and exact exports from a saved draft. These are worth keeping.

The system is not yet ready to run company work unattended. Successful document
generation does not establish that software was implemented, tested, deployed,
or that the client approved commercial terms. Each executable company task needs
an explicit tool contract, permissions, success checks, and a recovery path.

| Dimension | Assessment | Evidence |
| --- | --- | --- |
| Security | Needs work before shared company use | No user identity or per-project authorization in `app/main.py` |
| Correctness | Stronger proposal/BRD core; uneven remaining stages | Source validation and critic in the imported graph; other stages still produce templates with optional model hints |
| Performance | High risk for slow providers | Entire project generation waits inside one HTTP request; imported retry/model loops can exceed the hosting duration |
| Maintainability | Reasonable contracts; now two orchestration layers | Main workflow hosts proposal and BRD LangGraph subgraphs; source package is pinned and isolated |

## Findings, in priority order

### P1 — Company cases are accessible without staff authorization

`app/main.py:104` accepts project creation; the list/read/gate/artifact routes
do not authenticate callers or check project ownership. Anyone able to reach
an unprotected deployment can list client briefs and act on review gates.
CORS is not authorization. The original proposal agent likewise documents its
start endpoint as unauthenticated (`api/index.py:204` at the reference commit).

Implement staff login, company/workspace membership, reviewer roles, and
server-side ownership checks on every case, artifact, download, and gate route.
Do this before exposing actual client cases to a wider audience. Keep provider
keys and connector credentials server-side and separated by environment.

### P1 — Generation cannot reliably survive request termination

`app/workflow.py:132` executes analysis, specialist branches, writing and later
stages synchronously. Its snapshots do not checkpoint each nested specialist.
`app/main.py:120` has a `/run` helper that currently returns saved state rather
than recovering failed work. A function timeout can leave a case running with
no automatic continuation; a successful parallel result can be lost if its
partner fails before both are attached.

The integration currently uses the original generation graph as a subroutine
(`app/proposal_integration.py:68`), not its standalone checkpointer service.
The reference repository has a persistent SQLite/PostgreSQL graph runtime;
porting that state service is the next reliability step, along with a durable
job queue or workflow runner. Give each node a task ID, persist output before
advancing, and retry failed nodes without repeating successful provider calls.

### P1 — Provider attempts lack an overall case budget

`app/proposal_agent/proposal_workflow.py:23` rotates keys and models with
90-second attempts and optional waits. The writers also retry separately.
Proposal and BRD each run multiple specialists and critic packets. Even
finite per-call retries can overrun a 300-second serverless request.

Define an overall task deadline, token/cost budget, permitted model list, bounded
backoff, and cancellation behavior. Rate-limit errors should produce a durable
retry state. Benchmark complete cases with the intended model, not only unit
fixtures. Avoid unreviewed fallback models for sensitive company documents.

### P2 — Remaining agents do not execute engineering work

`app/agents.py` builds a generic architecture/backlog, QA plan and documentation
plan. Registry entries such as `github`, `playwright`, and `test_runner` are
descriptions of allowed tools, not bound callable tools. No repository edits,
browser tests, or deployments occur when those agents run.

Add tools one business workflow at a time. A development task needs an isolated
repository checkout, a concrete patch, tests, and a reviewable pull request.
A testing task needs executable scenarios and stored test results. A support or
CRM task needs a validated connector request and a verified resulting record.
Expose separate statuses for drafted, executed, verified, and approved work.

### P2 — Silent fallback can conceal an unavailable model

`app/agents.py:59` returns deterministic defaults after model/parse errors.
The health endpoint currently indicates key configuration rather than successful
inference. Template mode is valuable for local use, but it is not proof that an
AI specialist completed a case.

Record provider/model, attempt count, latency, token usage when supplied,
fallback reason, and generation mode per artifact. Offer a strict mode for
company deliverables that fails visibly instead of silently using a template.
The integrated proposal/BRD path already refuses failed quality checks and
does not silently replace a failed live graph with template output.

### P2 — Source fidelity and quality need company-specific evaluations

The original graph has useful coverage, reference, repair and schema checks.
They should be tested against an approved collection of real company scenarios.
The current fixtures and simulated model results cannot establish live document
quality, completeness of complex requirements, or correct product capabilities.

Create a redacted evaluation set containing expected requirements, exclusions,
client values, unresolved questions, unsupported-product traps, injection text,
and expected commercial treatment. Measure coverage, unsupported commitments,
source accuracy, reviewer corrections, and case completion rate. A model critic
can add feedback; deterministic and expert checks should determine acceptance.

## Changes completed in this integration

- Imported the proposal/BRD generation core, schemas, specialists, critic,
  restricted repair, scope checks, reference datasets and brand assets into
  `app/proposal_agent/`, isolated through relative imports. The provenance file
  records the exact upstream revision and adaptations.
- Connected its requirement analysis, proposal writer and BRD writer to the
  main workflow. Both document branches share one analyzed requirement context.
- Preserved specialist findings, source links, quality reports and graph trace
  in saved artifacts. Rejected/failed drafts cannot become successful artifacts.
- Passed proposal and BRD artifacts into downstream model prompts.
- Added Word downloads. Integrated proposal/BRD documents use their original
  branded renderer and saved content; exports never call a model again.
- Made template requirement IDs stable when source statements are reordered.
- Kept fixed work-hour targets out of the new workflow. Historical records are
  not treated as approved client estimates or rates.

No source account credentials were copied. Telegram messaging, CRM writes,
the separate PHP PDF service, and production graph schema migrations were
not activated as part of the local integration. The source's DOCX/HTML renderers
are included; only DOCX downloads are exposed in the new workspace.

## Configuration and verification

`PROPOSAL_ENGINE=auto` uses the integrated graph when `GROQ_API_KEY` is set.
`PROPOSAL_ENGINE=template` keeps deterministic proposal/BRD generation.
`PROPOSAL_ENGINE=langgraph` explicitly requires the Groq key. Restart after
changing provider settings. The integrated graph currently uses the original
Groq transport; an OpenAI-only key does not activate that graph.

46 offline regression tests passed, including ported source graph/grounding
tests, blocked quality failures, integration routing, stable IDs and branded
DOCX export. Provider responses in integration tests are simulated. The supplied test key is now configured in private environment files. Groq model
discovery, a live GPT-OSS 120B structured-output request, the application provider
transport, and live chatbot intake passed. Full proposal/BRD generation quality,
complete-case latency, and a Vercel cloud build remain unverified.

## Recommended implementation sequence

1. Staff identity and workspace authorization.
2. Durable task runner, nested checkpoints and real retry/resume endpoints.
3. Strict provider telemetry and case/model budget enforcement.
4. Approved company knowledge: capabilities, completed projects, commercial
   policies, and document templates with source/version controls.
5. Task evaluations and reviewer dashboards, followed by one executable
   business integration at a time.

For example, make proposal preparation the first complete workflow:
inquiry → source register → clarification → proposal/BRD specialists →
source checks → critic/repair → commercial review → approved Word/PDF →
explicitly authorized CRM update or client delivery. Then extend to engineering
and other company tasks with the same execution and verification discipline.
