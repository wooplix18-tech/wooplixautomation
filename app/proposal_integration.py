"""Connect the reviewed proposal-agent graph to the shared project workflow."""

import copy
import os
from collections import defaultdict

from .models import Artifact, Project, ProjectInput, RequirementItem
from . import progress


def proposal_engine() -> str:
    requested = os.getenv("PROPOSAL_ENGINE", "auto").strip()
    if requested not in {"auto", "langgraph", "template"}:
        raise ValueError("PROPOSAL_ENGINE must be auto, langgraph, or template.")
    if requested == "langgraph" and not os.getenv("GROQ_API_KEY", "").strip():
        raise ValueError("The integrated proposal agent requires GROQ_API_KEY.")
    return "langgraph" if requested == "langgraph" or (requested == "auto" and os.getenv("GROQ_API_KEY", "").strip()) else "template"


def analyze_requirements(project_input: ProjectInput) -> dict:
    from .proposal_agent import agent_graph, multi_agent_tools

    source = project_input.brief
    # Include structured client answers in the same canonical source.
    for field in ("goals", "target_users", "platforms", "integrations", "constraints"):
        values = getattr(project_input, field)
        if values:
            source += "\n" + field.replace("_", " ").title() + ":\n" + "\n".join(values)
    analysis, trace = agent_graph.run_analysis(source, [], "", [])
    # Historic examples are not approved estimates for this client.
    analysis["duration_estimates"] = []
    context = {"requirement": source, "source": project_input.project_name,
               "analysis": analysis, "completed": [], "timing_sources": [], "crm": ""}
    rows = multi_agent_tools.requirement_register(context)
    requirements = [RequirementItem(id=row["id"], category=row["area"], statement=row["requirement"],
                                    source=row["source"], acceptance_criteria=["Demonstrate the requested behavior: " + row["requirement"]]) for row in rows]
    return {"requirements": requirements, "questions": [q["question"] for q in analysis["questions"]],
            "proposal_context": context, "analysis_trace": trace}


class IntegratedDocumentAgent:
    def __init__(self, kind: str):
        self.kind = kind
        self.name = "proposal_generation_agent" if kind == "proposal" else "business_requirements_agent"

    def run(self, project: Project) -> Artifact:
        from .proposal_agent import multi_agent_graph

        context = copy.deepcopy(project.metrics.get("proposal_context"))
        if not context:
            # Existing projects can be resumed without losing their stable register.
            grouped = defaultdict(list)
            for requirement in project.requirements:
                grouped[requirement.category].append(requirement.statement)
            context = {"requirement": project.input.brief, "source": project.input.project_name,
                       "analysis": {"summary": project.input.brief[:1000],
                                    "requirement_sections": [{"product": key, "requirements": values} for key, values in grouped.items()],
                                    "questions": [], "duration_estimates": [], "commercial_categories": []},
                       "completed": [], "timing_sources": [], "crm": ""}
        if project.revision_feedback:
            context["requirement"] += "\nClient revision request: " + project.revision_feedback
        answers = {q["id"]: "Leave open for discovery" for q in context["analysis"].get("questions", [])}
        # Human clarification responses remain authoritative when available.
        for decision in project.decisions:
            for key, value in decision.get("answers", {}).items():
                if key in answers and value.strip():
                    answers[key] = value
        try:
            initial = {"mode": "case", "document_type": self.kind, "requirement": context["requirement"],
                  "source": context["source"], "context": context, "analysis": context["analysis"],
                  "answers": answers, "precomputed_context": True, "require_human_review": False,
                  "trace": [], "repair_count": 0}
            graph = multi_agent_graph.build_graph()
            if not progress.enabled():
                result = graph.invoke(initial, {"recursion_limit": 60})
            else:
                result = {}
                for mode, update in graph.stream(initial, {"recursion_limit": 60}, stream_mode=["tasks", "updates", "values"]):
                    if mode == "values":
                        result = update
                    elif mode == "tasks" and "input" in update:
                        progress.emit("subagent_started", f"{update['name']} started.", subagent=update["name"], task_id=update["id"])
                    elif mode == "updates":
                        for name, fields in update.items():
                            if name.startswith("__") or not isinstance(fields, dict):
                                continue
                            progress.emit("subagent_completed", f"{name} checkpoint produced.", subagent=name,
                                          output_fields=list(fields), trace=fields.get("trace", [])[-3:])
                progress.emit("model_completed", "Proposal-agent graph completed its source and quality checks.", engine="langgraph")
        except Exception as exc:
            raise ValueError("The integrated proposal/BRD agent could not complete generation. Check the provider and retry.") from exc
        key = "proposal" if self.kind == "proposal" else "document"
        if result.get("status") != "draft_ready" or not result.get("quality_report", {}).get("passed"):
            raise ValueError("The integrated document failed source or quality checks. It was not accepted as a completed deliverable.")
        content = result[key]
        content["integration"] = {"engine": "proposalagent-langgraph", "source_commit": "0ab21f9c70ced79c93bed2b18f1f71d6831c259a", "trace": result.get("trace", []), "status": "quality_checked_draft"}
        content["shared_requirement_links"] = [{"id": r.id, "requirement": r.statement} for r in project.requirements]
        return Artifact(kind=self.kind, title=f"{'Proposal' if self.kind == 'proposal' else 'Business Requirements Document'} — {project.input.project_name}", generated_by=[self.name, "solution_architect", "delivery_estimator", "commercial_analyst", "risk_and_dependency", "quality_critic"], content=content)
