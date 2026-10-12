"""Export the saved artifact without regenerating or contacting a model."""

import io
import json
import tempfile
from pathlib import Path

from docx import Document

from .models import Artifact


def artifact_docx(artifact: Artifact) -> bytes:
    if artifact.content.get("integration", {}).get("engine") == "proposalagent-langgraph":
        from .proposal_agent import business_requirements_agent, wooplix_agent

        renderer = wooplix_agent.build_docx if artifact.kind == "proposal" else business_requirements_agent.build_docx
        with tempfile.TemporaryDirectory(prefix="wooplix-export-") as folder:
            output = Path(folder) / "document.docx"
            renderer(artifact.content, str(output))
            return output.read_bytes()
    document = Document()
    document.add_heading(artifact.title, 0)
    document.add_paragraph("Wooplix Technologies Private Limited")
    document.add_paragraph(f"Draft · version {artifact.version}")
    for key, value in artifact.content.items():
        if key in {"planning_target", "traditional_hours", "ai_assisted_hours", "human_involvement_hours"} or key.endswith("_hours_target"):
            continue
        document.add_heading(key.replace("_", " ").title(), 1)
        document.add_paragraph(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
    output = io.BytesIO()
    document.save(output)
    return output.getvalue()
