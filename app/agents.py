from __future__ import annotations

import hashlib
import json
import os
import re
import ssl
import certifi
from groq import Groq, GroqError
from typing import Any
from urllib.request import Request, urlopen

from .models import Artifact, Project, RequirementItem


class AIClient:
    """Optional OpenAI-compatible adapter with safe deterministic fallbacks.

    The product remains runnable without credentials. When a provider key is
    present, agents ask for a JSON enhancement and use it only when the answer
    can be parsed; the workflow's typed deterministic output remains the source
    of truth for required fields.
    """

    def __init__(self) -> None:
        groq_key = os.getenv("GROQ_API_KEY", "").strip()
        openai_key = os.getenv("OPENAI_API_KEY", "").strip()
        self.api_key = groq_key or openai_key
        self.provider = "groq" if groq_key else "openai"
        self.base_url = (
            os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
            if groq_key
            else os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        ).rstrip("/")
        self.model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b") if groq_key else os.getenv(
            "AI_MODEL", "gpt-4o-mini"
        )

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def json(self, system: str, prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return fallback
        payload = {
                "model": self.model,
                "temperature": 0.2,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            }
        if self.provider == "groq":
            payload["max_completion_tokens"] = 4096
            if self.model.startswith("openai/gpt-oss"):
                payload["reasoning_effort"] = "low"
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            if self.provider == "groq":
                # Groq's SDK appends /openai/v1 itself; the saved setting is
                # an OpenAI-compatible base URL used by the former adapter.
                sdk_base_url = self.base_url.removesuffix("/openai/v1")
                with Groq(api_key=self.api_key, base_url=sdk_base_url, timeout=45, max_retries=0) as client:
                    response = client.chat.completions.create(**payload)
                content = response.choices[0].message.content
            else:
                context = ssl.create_default_context(cafile=certifi.where())
                with urlopen(request, timeout=45, context=context) as response:  # noqa: S310 - configured endpoint
                    raw = json.loads(response.read().decode("utf-8"))
                content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            return parsed if isinstance(parsed, dict) else fallback
        except (GroqError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            return fallback


def _strings(value: Any) -> list[str]:
    """Ignore malformed optional provider fields instead of failing a workflow."""
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:42] or "requirement"


def _sentences(text: str) -> list[str]:
    lines = [line.strip(" -*\t") for line in text.splitlines() if line.strip()]
    if len(lines) > 1:
        return lines
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _requirement_id(statement: str, index: int) -> str:
    normalized = " ".join(statement.casefold().split())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10].upper()
    return f"REQ-{digest}"


class RequirementsAgent:
    name = "requirement_analyst"

    def __init__(self, client: AIClient) -> None:
        self.client = client

    def run(self, project_input: Any) -> dict[str, Any]:
        from .proposal_integration import analyze_requirements, proposal_engine
        if proposal_engine() == "langgraph":
            return analyze_requirements(project_input)
        source_lines = _sentences(project_input.brief)
        source_lines = [line for line in source_lines if not re.match(r"^(?:project(?: name)?|title)\s*:|^Additional information from the client:\s*$", line, re.I)]
        supplied = [
            ("goal", goal) for goal in project_input.goals
        ] + [("integration", integration) for integration in project_input.integrations]
        statements: list[tuple[str, str]] = supplied + [("scope", line) for line in source_lines]
        seen: set[str] = set()
        requirements: list[RequirementItem] = []
        for category, statement in statements:
            normalized = " ".join(statement.lower().split())
            if len(normalized) < 8 or normalized in seen:
                continue
            seen.add(normalized)
            inferred_category = category
            if any(word in normalized for word in ("login", "user", "role", "permission")):
                inferred_category = "identity"
            elif any(word in normalized for word in ("payment", "checkout", "invoice")):
                inferred_category = "commerce"
            elif any(word in normalized for word in ("report", "dashboard", "analytics")):
                inferred_category = "reporting"
            elif any(word in normalized for word in ("api", "integrat", "webhook")):
                inferred_category = "integration"
            requirements.append(
                RequirementItem(
                    id=_requirement_id(statement, len(requirements) + 1),
                    category=inferred_category,
                    statement=statement,
                    priority="must" if category in {"goal", "scope"} else "should",
                    acceptance_criteria=[
                        f"The delivery team can demonstrate: {statement.rstrip('.')}.",
                        "The client confirms the behavior during user acceptance testing.",
                    ],
                )
            )
        if not requirements:
            requirements.append(
                RequirementItem(
                    id="REQ-01-brief",
                    category="scope",
                    statement=project_input.brief,
                    acceptance_criteria=["The delivered scope is reviewed against the client brief."],
                )
            )
        questions: list[str] = []
        if not project_input.target_users:
            questions.append("Who are the primary user groups and what job should each group complete?")
        if not project_input.platforms:
            questions.append("Which launch platforms and browsers/devices are in scope?")
        if not project_input.integrations:
            questions.append("Which existing systems, data sources, or third-party services must connect?")
        if project_input.timeline == "To be confirmed":
            questions.append("What launch date or milestone is the team planning toward?")
        model_hint = self.client.json(
            "You are a requirements analyst. Return JSON only with an additional_questions array. "
            "Do not invent scope; ask only about ambiguities visible in the supplied brief.",
            json.dumps({"brief": project_input.brief, "known_questions": questions}),
            {},
        )
        for question in _strings(model_hint.get("additional_questions")):
            if isinstance(question, str) and question.strip() and question not in questions:
                questions.append(question.strip())
        return {"requirements": requirements, "questions": questions}


class BaseAgent:
    name = "agent"

    def __init__(self, client: AIClient) -> None:
        self.client = client

    def artifact(self, kind: str, title: str, content: dict[str, Any], generated_by: list[str]) -> Artifact:
        return Artifact(kind=kind, title=title, content=content, generated_by=generated_by)

    def model_hint(self, instruction: str, project: Project) -> dict[str, Any]:
        return self.client.json(
            "You are one bounded specialist in a software delivery workflow. "
            "Return a small JSON object only. Do not invent client commitments, prices, dates, or scope. "
            "Do not add work-hour targets or comparisons.",
            json.dumps({"instruction": instruction, "brief": project.input.brief, "requirements": [item.statement for item in project.requirements], "revision_feedback": project.revision_feedback,
                        "upstream_artifacts": {a.kind: json.dumps(a.content, ensure_ascii=False)[:6000] for a in project.artifacts if a.kind in {"proposal", "brd", "ui_ux", "implementation", "qa_uat"}}}),
            {},
        )


class ProposalAgent(BaseAgent):
    name = "proposal_generation_agent"

    def run(self, project: Project) -> Artifact:
        inp = project.input
        requirements = project.requirements
        fallback = {
            "executive_summary": (
                f"{inp.client_name} can use Wooplix's AI-assisted delivery system to take "
                f"{inp.project_name} from an agreed brief to a reviewable software release."
            ),
            "objectives": inp.goals or ["Convert the client brief into an executable, testable product plan."],
            "in_scope": [item.statement for item in requirements],
            "out_of_scope": [
                "Unapproved third-party licensing and production data migration",
                "Features not represented in the source requirement register",
            ],
            "deliverables": [
                "Validated BRD and requirement traceability register",
                "UI/UX direction and screen-level interaction specification",
                "Implementation blueprint and starter backlog",
                "Automated test strategy, UAT pack, and handover documentation",
            ],
            "delivery_model": "AI agents work in bounded stages; human reviewers approve scope, design, release quality, and handover.",
            "assumptions": [
                "Client feedback is consolidated at each human review gate.",
                "Credentials, source data, brand assets, and integration documentation are supplied when requested.",
                f"Launch timing is {inp.timeline} and budget is {inp.budget}.",
            ],
            "open_decisions": [
                "Confirm primary users, launch platforms, integration boundaries, and release date.",
                "Confirm the proposed architecture and acceptance criteria before implementation begins.",
            ],
        }
        model_hint = self.model_hint("Suggest a more precise executive summary and up to three open decisions.", project)
        if isinstance(model_hint.get("executive_summary"), str) and model_hint["executive_summary"].strip():
            fallback["executive_summary"] = model_hint["executive_summary"].strip()
        if isinstance(model_hint.get("open_decisions"), list):
            decisions = [value.strip() for value in model_hint["open_decisions"] if isinstance(value, str) and value.strip()]
            if decisions:
                fallback["open_decisions"] = decisions[:3]
        return self.artifact("proposal", f"Proposal — {inp.project_name}", fallback, [self.name])


class BRDAgent(BaseAgent):
    name = "business_requirements_agent"

    def run(self, project: Project) -> Artifact:
        inp = project.input
        requirement_rows = [
            {
                "id": item.id,
                "category": item.category,
                "requirement": item.statement,
                "priority": item.priority,
                "acceptance_criteria": item.acceptance_criteria,
                "source": item.source,
            }
            for item in project.requirements
        ]
        content = {
            "document_control": {
                "project": inp.project_name,
                "client": inp.client_name,
                "project_type": inp.project_type,
                "status": "Draft for business review",
            },
            "problem_statement": inp.brief,
            "business_goals": inp.goals,
            "personas": inp.target_users or ["Primary end users — to be confirmed"],
            "functional_requirements": requirement_rows,
            "non_functional_requirements": [
                "Responsive and accessible experience appropriate to the selected platforms.",
                "Observable errors, audit-friendly changes, and secure handling of credentials.",
                "Release process supports repeatable testing and rollback planning.",
            ],
            "constraints": inp.constraints,
            "integrations": inp.integrations or ["Integration inventory to be confirmed"],
            "assumptions": ["Unanswered clarification items remain open decisions, not approvals."],
            "traceability": [
                {"requirement_id": item.id, "mapped_stages": ["design", "development", "qa", "uat"]}
                for item in project.requirements
            ],
        }
        model_hint = self.model_hint("Suggest non-functional requirements that are directly relevant to this brief.", project)
        extra_nfr = _strings(model_hint.get("non_functional_requirements"))
        if extra_nfr:
            content["non_functional_requirements"].extend(extra_nfr[:3])
        return self.artifact("brd", f"Business Requirements Document — {inp.project_name}", content, [self.name])


class UXDesignAgent(BaseAgent):
    name = "ui_ux_design_agent"

    def run(self, project: Project) -> Artifact:
        inp = project.input
        screens = [
            {"name": "Entry / landing", "purpose": "Explain value and route the user to the primary action."},
            {"name": "Authentication / access", "purpose": "Provide secure access where the requirement needs user identity."},
            {"name": "Primary workflow", "purpose": "Let the user complete the core job described in the requirement register."},
            {"name": "Status / confirmation", "purpose": "Make progress, errors, and completion visible."},
        ]
        content = {
            "design_principles": [
                "Make the primary user job obvious within one screen.",
                "Use accessible contrast, keyboard navigation, readable type, and clear focus states.",
                "Design loading, empty, error, and success states before happy-path polish.",
            ],
            "personas": inp.target_users or ["Primary user — validate in the requirements gate"],
            "journeys": [{"name": "Core outcome", "steps": ["Discover", "Start", "Complete", "Confirm"]}],
            "screens": screens,
            "responsive_strategy": "Mobile-first layout for websites; adapt the same information architecture to native mobile or internal-tool constraints.",
            "design_system": {
                "brand_input": inp.brand_preferences or "Brand direction to be supplied",
                "components": ["Navigation", "Form controls", "Cards", "Tables", "Alerts", "Modal / confirmation"],
            },
            "prototype_handoff": ["Annotated flows", "Component states", "Content and validation rules", "Accessibility checklist"],
            "requirement_links": [item.id for item in project.requirements],
        }
        model_hint = self.model_hint("Suggest up to three accessibility or edge-state design checks for this brief.", project)
        extra_checks = _strings(model_hint.get("design_checks"))
        if extra_checks:
            content["design_principles"].extend(extra_checks[:3])
        return self.artifact("ui_ux", f"UI/UX Design Specification — {inp.project_name}", content, [self.name])


class DevelopmentAgent(BaseAgent):
    name = "software_development_agent"

    def run(self, project: Project) -> Artifact:
        inp = project.input
        frontend = "Next.js / React" if inp.project_type == "website" else "React Native" if inp.project_type == "mobile_app" else "TypeScript application"
        content = {
            "architecture": {
                "style": "Modular client/API architecture with a typed domain layer",
                "frontend": frontend,
                "backend": "FastAPI or equivalent typed service, subject to technical review",
                "data": "Managed relational data store with migrations and backup strategy",
                "deployment": "CI/CD pipeline with preview, staging, production, and rollback controls",
            },
            "implementation_principles": [
                "Keep business rules independent from UI components.",
                "Use typed contracts at API and persistence boundaries.",
                "Instrument critical user journeys and failures before release.",
            ],
            "backlog": [
                {"id": f"DEV-{index:02d}", "title": item.statement, "requirement_id": item.id, "definition_of_done": item.acceptance_criteria}
                for index, item in enumerate(project.requirements, 1)
            ],
            "starter_repository": {
                "directories": ["src/domain", "src/features", "src/components", "tests/unit", "tests/integration", "docs"],
                "quality_gates": ["format", "type-check", "unit tests", "integration tests", "security scan", "preview deployment"],
            },
            "api_contracts": [
                {"method": "GET", "path": "/health", "purpose": "Service readiness"},
                {"method": "POST", "path": "/resource", "purpose": "Create the core domain resource — finalize after BRD approval"},
            ],
            "open_technical_decisions": [
                "Confirm hosting, authentication provider, data residency, and external integration credentials.",
                "Confirm whether the proposed stack matches the client's existing engineering standards.",
            ],
        }
        model_hint = self.model_hint("Suggest up to three technical decisions that must be confirmed before implementation.", project)
        extra_decisions = _strings(model_hint.get("technical_decisions"))
        if extra_decisions:
            content["open_technical_decisions"].extend(extra_decisions[:3])
        return self.artifact("implementation", f"Software Implementation Blueprint — {inp.project_name}", content, [self.name])


class QAAgent(BaseAgent):
    name = "testing_uat_agent"

    def run(self, project: Project) -> Artifact:
        cases = []
        for index, item in enumerate(project.requirements, 1):
            cases.append(
                {
                    "id": f"TC-{index:02d}",
                    "requirement_id": item.id,
                    "scenario": item.statement,
                    "expected_result": item.acceptance_criteria[0] if item.acceptance_criteria else "Client-confirmed expected behavior is met.",
                    "type": "functional + UAT",
                }
            )
        content = {
            "strategy": ["Unit tests", "API/integration tests", "Critical-path browser or device tests", "Accessibility and security checks", "Client UAT"],
            "test_cases": cases,
            "release_gates": [
                "No open blocker or critical defects",
                "Requirement traceability is complete",
                "Smoke tests pass in the release environment",
                "Client UAT decision is recorded",
            ],
            "uat_script": [
                "Review the scope and known open decisions.",
                "Execute each mapped core journey with representative data.",
                "Record pass, fail, or change request with evidence.",
                "Approve release or return a prioritized defect list.",
            ],
            "defect_policy": "Blocking defects pause handover; non-blocking observations become a prioritized backlog with an owner confirmed by the human reviewer.",
        }
        model_hint = self.model_hint("Suggest up to three extra UAT scenarios, each as a string, based on the brief.", project)
        for index, scenario in enumerate(_strings(model_hint.get("uat_scenarios")), len(cases) + 1):
            if isinstance(scenario, str) and scenario.strip():
                content["uat_script"].append(f"Execute additional scenario {index}: {scenario.strip()}")
        return self.artifact("qa_uat", f"Test Strategy & UAT Pack — {project.input.project_name}", content, [self.name])


class DocumentationAgent(BaseAgent):
    name = "documentation_training_agent"

    def run(self, project: Project) -> Artifact:
        content = {
            "documentation_set": [
                {"title": "Product overview", "audience": "Client stakeholders", "status": "Prepared from approved scope"},
                {"title": "User guide", "audience": "Operators and end users", "status": "Customize with final screenshots and terminology"},
                {"title": "Technical runbook", "audience": "Engineering / operations", "status": "Populate deployment secrets and environment values during handover"},
                {"title": "Support and incident guide", "audience": "Support team", "status": "Confirm escalation contacts and service expectations"},
            ],
            "training_plan": [
                "60-minute stakeholder walkthrough of completed journeys",
                "Role-based operator session with recorded examples",
                "Engineering handoff covering repository, environments, monitoring, and rollback",
            ],
            "knowledge_transfer_checklist": [
                "Repository and branch permissions transferred",
                "Environment variables and secret ownership documented",
                "Known issues and prioritized backlog reviewed",
                "Support and escalation contacts confirmed",
            ],
            "source_links": [artifact.kind for artifact in project.artifacts],
        }
        model_hint = self.model_hint("Suggest up to two role-specific training tips for this brief.", project)
        tips = _strings(model_hint.get("training_tips"))
        if tips:
            content["training_plan"].extend(tips[:2])
        return self.artifact("documentation", f"Documentation & Training Plan — {project.input.project_name}", content, [self.name])


class HandoverAgent(BaseAgent):
    name = "human_handover_agent"

    def run(self, project: Project) -> Artifact:
        if project.input.execution_mode == "automatic":
            return self.artifact(
                "handover", f"Delivery Pack — {project.input.project_name}",
                {
                    "release_status": "Delivery planning pack generated",
                    "delivered_artifacts": [artifact.kind for artifact in project.artifacts],
                    "requirements": [item.id for item in project.requirements],
                    "open_questions": project.metrics.get("open_questions", []),
                    "next_steps": ["Implement the generated blueprint", "Execute the test and UAT plan against the implementation", "Confirm release and operational ownership"],
                    "implementation_status": "Blueprint generated; application code has not been built or deployed",
                    "uat_status": "Test plan generated; acceptance tests have not been executed",
                }, [self.name],
            )
        return self.artifact(
            "handover",
            f"Client Handover Record — {project.input.project_name}",
            {
                "release_status": "Approved for handover",
                "handover_checklist": [
                    "Final UAT decision recorded",
                    "Approved artifacts and requirement traceability delivered",
                    "Repository, environments, and operational ownership transferred",
                    "Training and support materials reviewed",
                    "Post-launch backlog and next review date agreed",
                ],
                "delivered_artifacts": [artifact.kind for artifact in project.artifacts],
                "human_signoff": "Recorded through the UAT gate",
            },
            [self.name],
        )


def build_agents(client: AIClient | None = None) -> dict[str, Any]:
    client = client or AIClient()
    from .proposal_integration import IntegratedDocumentAgent, proposal_engine
    integrated = proposal_engine() == "langgraph"
    return {
        "requirements": RequirementsAgent(client),
        "proposal": IntegratedDocumentAgent("proposal") if integrated else ProposalAgent(client),
        "brd": IntegratedDocumentAgent("brd") if integrated else BRDAgent(client),
        "ui_ux": UXDesignAgent(client),
        "implementation": DevelopmentAgent(client),
        "qa_uat": QAAgent(client),
        "documentation": DocumentationAgent(client),
        "handover": HandoverAgent(client),
    }
