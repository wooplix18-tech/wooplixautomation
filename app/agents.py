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
from .progress import emit
from .groq_limiter import wait_for_groq_slot


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
            emit("template", "No model key configured; deterministic template used.")
            return fallback
        emit("model_started", "Calling the model provider.", provider=self.provider, model=self.model)
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
                    if self.provider == "groq":
                        wait_for_groq_slot()
                    response = client.chat.completions.create(**payload)
                content = response.choices[0].message.content
            else:
                context = ssl.create_default_context(cafile=certifi.where())
                with urlopen(request, timeout=45, context=context) as response:  # noqa: S310 - configured endpoint
                    raw = json.loads(response.read().decode("utf-8"))
                content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            if not isinstance(parsed, dict):
                raise ValueError("Expected a JSON object")
        except (GroqError, OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            emit("fallback", "Provider unavailable or invalid JSON; template fallback used.", provider=self.provider, model=self.model)
            return fallback
        emit("model_completed", "Structured model response received.", provider=self.provider, model=self.model)
        return parsed


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
        source = inp.brief.lower()
        zoho_event = "zoho" in source and any(word in source for word in ("event", "backstage", "crm"))
        if zoho_event:
            commercial_labels = [
                "Zoho CRM implementation", "Zoho Marketing Automation implementation",
                "Zoho Projects implementation", "Zoho Creator and Backstage implementation",
                "Zoho Analytics implementation", "Integration and customization",
                "WhatsApp and SMS integration", "Data migration and cleansing", "Training",
                "Post-implementation support", "Additional licenses and add-ons",
            ]
            scope_by_area = {
                "Zoho CRM": "Lead capture and BDM assignment; duplicate-company handling; multiple locations and contacts; event and batch records; existing-data cleansing; follow-up automation.",
                "Zoho Marketing Automation": "Email, WhatsApp and SMS campaigns; marketing journeys; audience segmentation; follow-up automation and campaign tracking.",
                "Zoho Projects": "Event-based project templates; tasks, milestones and assignment; planned vs actual hours; billable and non-billable time; timesheets, work notes, automation, dashboards, reports and team training.",
                "Zoho Creator and Backstage": "Discovery-led recommendation and implementation for event management, custom applications, forms, workflows, CRM integration, reports, dashboards and user roles.",
                "Zoho Analytics": "Integrated CRM, BDM, lead, event, campaign, Projects, effort and team reporting, including a management dashboard.",
            }
            scope = [{"area": area, "scope": details} for area, details in scope_by_area.items()]
            delivery_phases = [
                "Discovery, process mapping and solution design", "CRM configuration and data cleansing",
                "Marketing automation and messaging integrations", "Projects setup and event templates",
                "Creator / Backstage application design and build", "Analytics integration and dashboards",
                "End-to-end validation, practical training and go-live readiness",
            ]
            commercial_items = [{"item": label, "amount": "", "basis": "Separate estimate after discovery and license review"}
                                for label in commercial_labels]
            fallback = {
                "executive_summary": "Wooplix will plan and implement an integrated Zoho One operating system for the client’s event-management business, covering CRM, marketing, project delivery, event applications, analytics, communications, migration and practical training.",
                "objectives": ["Connect the requested Zoho applications around shared event, company, location, contact and campaign data.", "Give the team usable workflows, reports and practical training, with scope and costs reviewed before implementation."],
                "in_scope": scope,
                "delivery_approach": [{"phase": phase, "duration": "To be confirmed after discovery", "review_point": "Client review and approval before the next phase"} for phase in delivery_phases],
                "commercials": {"items": commercial_items, "total": "", "note": "No implementation price or license cost has been invented. Confirm volumes, editions, messaging providers and delivery scope before quoting."},
                "training_plan": ["Role-based working sessions for CRM, Marketing Automation, Projects, Creator, Analytics, automations and reports.", "Use the client’s event, lead, campaign and project scenarios; include practice exercises and question time.", "Confirm attendees, session count, format and schedule during discovery."],
                "support": "Define the post-implementation support period, channels, service hours, response targets and included change allowance before approval.",
                "assumptions": ["The client will provide Zoho One access, current CRM exports, data definitions, user/role lists, event processes and messaging-provider details.", "WhatsApp/SMS provider availability, consent rules, message charges and Zoho edition limits will be validated before configuration.", "Existing data will be profiled first; cleansing rules and migration acceptance will be agreed before records are changed.", "Creator and Backstage workflows are discovery items because the application has not yet been started."],
                "open_decisions": ["Confirm user counts, Zoho editions, BDM assignment rules, event/batch relationships and duplicate-resolution rules.", "Confirm WhatsApp/SMS provider, consent and opt-out handling, message volumes and who supplies templates.", "Confirm Creator versus Backstage boundaries, migration volume, dashboard KPIs, training format and support period."],
                "next_step": "Run a discovery workshop, validate data and licensing, then approve the detailed scope, phased schedule and itemized commercial estimate.",
            }
            return self.artifact("proposal", f"Zoho One Implementation Proposal — {inp.project_name}", fallback, [self.name])
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
        sources = {artifact.kind: artifact for artifact in project.artifacts}
        brd = sources.get("brd").content if sources.get("brd") else {}
        ux = sources.get("ui_ux").content if sources.get("ui_ux") else {}
        implementation = sources.get("implementation").content if sources.get("implementation") else {}
        qa = sources.get("qa_uat").content if sources.get("qa_uat") else {}

        requirements = brd.get("functional_requirements") or [
            {"id": item.id, "requirement": item.statement,
             "acceptance_criteria": item.acceptance_criteria}
            for item in project.requirements
        ]
        if not isinstance(requirements, list):
            requirements = []

        def clean_line(value: str) -> str:
            value = re.sub(r"^[\s•▪◦*-]+", "", value).strip()
            return re.sub(r"^\d+[.)]\s*", "", value).strip()

        def items_in_section(start_pattern: str, end_pattern: str) -> list[str]:
            found, values = False, []
            for raw_line in project.input.brief.splitlines():
                line = raw_line.strip()
                if re.match(start_pattern, line, re.I):
                    found = True
                    continue
                if found and re.match(end_pattern, line, re.I):
                    break
                if not found:
                    continue
                line = clean_line(line)
                if not line or line.endswith(":") or line.casefold().startswith(("please provide", "we require practical training")):
                    continue
                if line not in values:
                    values.append(line)
            return values

        training_topics = items_in_section(
            r"^(?:\d+[.)]\s*)?Training\s*$",
            r"^(?:\d+[.)]\s*)?Cost Proposal Required\s*$",
        )
        cost_categories = items_in_section(
            r"^(?:\d+[.)]\s*)?Cost Proposal Required\s*$",
            r"^(?:Please also provide|Main Objective)\b",
        )
        section_heading = re.compile(
            r"^(?:\d+[.)]\s*)?(?:our requirement|main objective|training|cost proposal required|"
            r"zoho (?:crm|marketing automation|projects|creator|analytics)(?:\s*[,—–-].*)?)$",
            re.I,
        )
        instruction_prefixes = (
            "we are using zoho", "we have not started", "we need your recommendation",
            "we require practical training", "please provide separate", "please also provide",
            "we want one integrated system",
        )
        supplied_integrations = {" ".join(item.casefold().split()) for item in project.input.integrations}
        supplied_cost_categories = {" ".join(item.casefold().split()) for item in cost_categories}
        filtered_requirements = []
        seen_requirements = set()
        for row in requirements:
            if not isinstance(row, dict):
                continue
            statement = row.get("requirement") or row.get("statement")
            if not isinstance(statement, str):
                continue
            statement = clean_line(statement)
            normalized = " ".join(statement.casefold().split())
            if (not normalized or section_heading.match(statement)
                    or normalized in supplied_integrations
                    or normalized in supplied_cost_categories
                    or normalized.startswith(instruction_prefixes)
                    or "training" in normalized):
                continue
            if normalized not in seen_requirements:
                seen_requirements.add(normalized)
                filtered_requirements.append({**row, "requirement": statement})
        requirements = filtered_requirements
        if not training_topics:
            training_topics = [item for item in project.input.integrations if item.casefold().startswith("zoho")]
        brd_requirements = brd.get("functional_requirements") or []
        if isinstance(brd_requirements, list):
            for row in brd_requirements:
                if not isinstance(row, dict):
                    continue
                topic = clean_line(str(row.get("requirement") or row.get("statement", "")))
                if ("training" in topic.casefold() and topic.casefold() not in {"training", "cost proposal required"}
                        and not topic.casefold().startswith(instruction_prefixes) and topic not in training_topics):
                    training_topics.append(topic)
        users = brd.get("personas") or project.input.target_users or ["Primary users — confirm before release"]
        if not isinstance(users, list):
            users = [users]
        users = [
            user.get("name") or user.get("role") or user.get("persona") or "User — confirm role"
            if isinstance(user, dict) else str(user)
            for user in users
        ]
        screens = ux.get("screens") or []
        journeys = ux.get("journeys") or []
        if not isinstance(screens, list):
            screens = []
        if not isinstance(journeys, list):
            journeys = []
        flow_steps = [step for journey in journeys if isinstance(journey, dict)
                      for step in journey.get("steps", []) if isinstance(step, str)]
        if not flow_steps:
            flow_steps = ["Start the task", "Complete the required steps", "Check the result"]

        user_tasks = []
        for row in requirements:
            if not isinstance(row, dict):
                continue
            criteria = row.get("acceptance_criteria", [])
            if isinstance(criteria, str):
                criteria = [criteria]
            elif not isinstance(criteria, list):
                criteria = []
            requirement_id = row.get("id")
            linked_screens = [screen.get("name") for screen in screens
                              if isinstance(screen, dict) and screen.get("name")
                              and requirement_id in screen.get("requirement_links", [])]
            user_tasks.append({
                "requirement_id": requirement_id or "Needs traceability",
                "task": row.get("requirement") or row.get("statement", "User task from the BRD"),
                "related_screens": linked_screens or "Map this requirement to a screen before release.",
                "acceptance_checks": [item for item in criteria if isinstance(item, str)],
                "status": "Draft — confirm exact controls and wording against the implemented product.",
            })

        test_cases = qa.get("test_cases") or []
        if not isinstance(test_cases, list):
            test_cases = []
        role_keywords = {
            "bdm": ("crm", "lead", "bdm", "contact", "company", "location"),
            "sales": ("crm", "lead", "bdm", "contact", "company", "location"),
            "marketing": ("marketing", "campaign", "audience", "journey", "whatsapp", "sms", "email"),
            "event": ("event", "project", "task", "milestone", "timesheet", "billable", "batch"),
            "operation": ("event", "project", "task", "milestone", "timesheet", "billable", "batch"),
            "admin": ("creator", "custom", "role", "workflow", "integration", "migration", "data"),
            "management": ("analytics", "dashboard", "report", "performance", "hours", "campaign"),
        }
        def training_requirements(user: str) -> list[dict[str, Any]]:
            rows = [row for row in requirements if isinstance(row, dict)]
            role = next((name for name in role_keywords if name in user.casefold()), None)
            if role:
                words = role_keywords[role]
                matched = [row for row in rows if any(
                    word in (row.get("requirement") or row.get("statement", "")).casefold()
                    for word in words
                )]
                if matched:
                    rows = matched
            return rows[:3]

        training = [{
            "audience": user,
            "practice": [
                {"requirement_id": row.get("id", "Needs traceability"),
                 "task": row.get("requirement") or row.get("statement", "Complete the core task"),
                 "success_check": ((row.get("acceptance_criteria") if isinstance(row.get("acceptance_criteria"), list)
                                    else [row.get("acceptance_criteria")]) or ["Confirm the expected result."])[0]}
                for row in training_requirements(user)
            ],
        } for user in users]

        documented_requirement_ids = {row.get("id") for row in requirements if row.get("id")}
        support_scenarios = [{
            "scenario": case.get("scenario", "Planned test scenario"),
            "requirement_id": case.get("requirement_id", "Needs traceability"),
            "expected_result": case.get("expected_result", "Confirm the expected result in the approved BRD."),
        } for case in test_cases if isinstance(case, dict)
            and case.get("requirement_id") in documented_requirement_ids]

        architecture = implementation.get("architecture", {})
        api_contracts = implementation.get("api_contracts", [])
        release_gates = qa.get("release_gates", [])
        sources_used = [{"artifact": kind, "title": artifact.title, "version": artifact.version}
                        for kind, artifact in sources.items()
                        if kind in {"proposal", "brd", "ui_ux", "implementation", "qa_uat"}]
        content = {
            "document_status": (
                "Planning draft generated from this project's saved artifacts. "
                "The system has not inspected or verified a built application. "
                "Confirm procedures, screenshots, technical settings, and support ownership before release."
            ),
            "project_overview": {
                "project": project.input.project_name,
                "purpose": project.input.brief,
                "users": users,
                "goals": project.input.goals,
                "platforms": project.input.platforms,
                "connected_systems": project.input.integrations,
            },
            "user_guide": {
                "purpose": "Help each intended user complete the tasks described in the BRD.",
                "audience": users,
                "getting_started": [
                    "Confirm the user has the access and account required by the approved product design.",
                    "Choose the task that matches the user's goal below.",
                    "Follow the approved interface and compare the result with the acceptance checks.",
                ],
                "proposed_journey_to_validate": flow_steps,
                "tasks": user_tasks,
                "screens_from_design": screens,
            },
            "technical_reference": {
                "status": "Proposed technical reference; verify against the source repository and deployed environment.",
                "architecture": architecture,
                "api_contracts": api_contracts,
                "quality_gates": implementation.get("starter_repository", {}).get("quality_gates", []),
                "release_checks": release_gates,
                "decisions_to_confirm": implementation.get("open_technical_decisions", []),
                "security_notes": [
                    "Store credentials in the approved secrets manager; do not copy secret values into this document.",
                    "Confirm authentication, access roles, backups, monitoring, and rollback against the deployed system.",
                ],
            },
            "support_runbook": {
                "status": "Draft from planned test cases; actual monitoring, contacts, and service targets are not configured here.",
                "triage_steps": [
                    "Record the affected user role, time, task, and steps to reproduce.",
                    "Compare the observed behavior with the related requirement and expected result.",
                    "Check the approved service status and redacted application logs.",
                    "Capture a redacted screenshot or log excerpt; never include passwords or API keys.",
                    "Escalate blocking or security issues to the confirmed service owner.",
                    "Record the resolution and update the known-issues list.",
                ],
                "known_scenarios": support_scenarios,
                "contacts_to_confirm": ["Product owner", "Technical owner", "Support escalation contact"],
                "service_targets": "Confirm response and recovery targets with the service owner.",
            },
            "training_guide": {
                "approach": "Use role-based practice tasks and acceptance checks; agree delivery format with the project owner.",
                "topics_requested": training_topics,
                "sessions": training,
                "materials_to_prepare": ["Approved interface screenshots", "Practice accounts or safe sample data", "Quick-reference task guide"],
            },
            "cost_proposal_request": {
                "requested_breakdown": cost_categories,
                "status": "Requested by the client; no implementation fees, license prices, or support rates were supplied or estimated by this documentation agent.",
            },
            "handover_checklist": [
                {"item": "Verify this guide against the implemented and deployed product", "status": "To do"},
                {"item": "Add approved screenshots and final interface terminology", "status": "To do"},
                {"item": "Confirm repository, environment, monitoring, backup, and rollback procedures", "status": "To do"},
                {"item": "Name product, technical, and support owners", "status": "To do"},
                {"item": "Review known issues and open decisions with the client", "status": "To do"},
            ],
            "open_items": [
                "Implementation code and deployed behavior were not available to this agent.",
                "Confirm screen-level steps and user permissions after the interface is built.",
                "Confirm support contacts, monitoring links, data retention, and recovery procedures.",
                "Clarify whether ‘Backstages’ means backstage event operations and define the Creator workflows it requires.",
                "Confirm Zoho editions, license/add-on prices, and WhatsApp/SMS providers before preparing a commercial quote.",
                "Agree the implementation timeline, milestones, and post-implementation support scope with the delivery team.",
            ],
            "source_artifacts": sources_used,
        }
        return self.artifact("documentation", f"Documentation & Training Pack — {project.input.project_name}", content, [self.name])


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
