"""Generate the architecture Word document and three embedded graph diagrams.

Requires requirements-dev.txt (Pillow) plus the application's python-docx.
Uses only docs/WORKFLOW_ARCHITECTURE.md; it never reads environment secrets.
"""

import argparse
import math
import re
from datetime import date
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def font(size, bold=False):
    try:
        return ImageFont.truetype(f"/System/Library/Fonts/Supplemental/Arial{' Bold' if bold else ''}.ttf", size)
    except OSError:
        return ImageFont.load_default(size=size)


def diagram(path, title, nodes, edges, size=(1500, 940)):
    image = Image.new("RGB", size, "#f5f7fb")
    draw = ImageDraw.Draw(image)
    draw.text((45, 28), title, fill="#18283f", font=font(32, True))
    for source, target in edges:
        a, b = nodes[source], nodes[target]
        ax, ay, aw, ah = a[:4]
        bx, by, bw, bh = b[:4]
        if abs(ay-by) > 60:
            start = (ax+aw/2, ay+ah) if by > ay else (ax+aw/2, ay)
            end = (bx+bw/2, by) if by > ay else (bx+bw/2, by+bh)
        else:
            start = (ax+aw, ay+ah/2) if bx > ax else (ax, ay+ah/2)
            end = (bx, by+bh/2) if bx > ax else (bx+bw, by+bh/2)
        draw.line([start, end], fill="#5e7592", width=4)
        angle = math.atan2(end[1]-start[1], end[0]-start[0])
        wing = [(end[0]-15*math.cos(angle+offset), end[1]-15*math.sin(angle+offset)) for offset in (-.5, .5)]
        draw.polygon([end, *wing], fill="#5e7592")
    for x, y, w, h, title, subtitle, human in nodes.values():
        fill, border = ("#fff5dd", "#c49b43") if human else ("#e6f4f0", "#438d7b")
        draw.rounded_rectangle([x,y,x+w,y+h], radius=16, fill=fill, outline=border, width=3)
        for index, line in enumerate(title.split("\n")):
            draw.text((x+w/2, y+25+index*27), line, fill="#18283f", font=font(23, True), anchor="mm")
        for index, line in enumerate(subtitle.split("\n")):
            draw.text((x+w/2, y+h-35+index*22), line, fill="#4d617d", font=font(18), anchor="mm")
    image.save(path)


def make_diagrams(folder):
    files = [folder / name for name in ("system-architecture.png", "delivery-graph.png", "document-subgraph.png")]
    system = {
        "browser": (450,110,590,120,"Browser: Workflow Studio","Canvas / node inspector / input and output",False),
        "api": (450,330,590,120,"FastAPI on Vercel or Python host","Queue / execute wave / decision / SSE",False),
        "db": (40,580,410,145,"Neon PostgreSQL","CAS snapshots / events / task outputs\nSQLite for local development",False),
        "runner": (530,580,430,145,"Checkpointed runner","Parallel nodes / leases / retries\nHTTP request or Python worker",False),
        "llm": (1040,580,410,145,"Specialists + LangGraph","Groq / OpenAI-compatible calls\nSaved documents and quality checks",False),
        "worker": (530,810,430,100,"Optional worker service","Schedules queued waves unattended",True),
    }
    diagram(files[0], "01 / Runtime architecture", system, [("browser","api"),("api","db"),("api","runner"),("runner","db"),("runner","llm"),("worker","runner")])
    delivery = {
        "r": (530,100,430,110,"Requirement analyst","Source register + open questions",False),
        "s": (530,260,430,110,"Scope confirmation","Human decision or explicitly skipped",True),
        "p": (110,445,500,110,"Proposal subgraph","Specialists / writer / critic",False),
        "b": (890,445,500,110,"BRD subgraph","Specialists / writer / critic",False),
        "u": (110,640,500,110,"UI / UX planning","Journeys, screens and states",False),
        "d": (890,640,500,110,"Development blueprint","Architecture, API and backlog plan",False),
        "q": (530,825,430,110,"QA / UAT plan","Scenarios and release criteria",False),
        "docs": (530,1000,430,110,"Documentation / training","User and operations plans",False),
        "review": (530,1175,430,110,"Delivery review + client UAT","Human gates in reviewed mode",True),
        "done": (530,1350,430,110,"Handover / planning pack","Completed or pack_ready",False),
    }
    diagram(files[1], "02 / Top-level dependency graph", delivery, [("r","s"),("s","p"),("s","b"),("p","u"),("b","u"),("p","d"),("b","d"),("u","q"),("d","q"),("q","docs"),("docs","review"),("review","done")], (1500,1510))
    nested = {
        "input": (450,100,600,100,"Analyzed scope + client answers","Shared requirement register",False),
        "sup": (450,270,600,100,"Document supervisor","Focused specialist task contracts",False),
        "sol": (30,455,325,115,"Solution","Proposed approach",False),
        "del": (400,455,325,115,"Delivery","Dependencies",False),
        "com": (770,455,325,115,"Commercial","Quote conditions",False),
        "risk": (1140,455,325,115,"Risk","Mitigations",False),
        "join": (450,645,600,110,"Reconcile + document writer","Typed source checks and requirement links",False),
        "critic": (450,835,600,110,"Validate + independent critic","One repair then recheck / reject",True),
        "out": (450,1025,600,110,"Quality-checked draft","Saved content / trace / branded Word export",False),
    }
    diagram(files[2], "03 / Proposal and BRD subgraph", nested, [("input","sup"), *[("sup",k) for k in ("sol","del","com","risk")], *[(k,"join") for k in ("sol","del","com","risk")], ("join","critic"),("critic","out")], (1500,1180))
    return files


def add_text(paragraph, text):
    # Minimal inline Markdown support for the deliberately simple source guide.
    for index, chunk in enumerate(re.split(r"(\*\*.*?\*\*|`[^`]+`)", text)):
        if chunk.startswith("**"):
            paragraph.add_run(chunk[2:-2]).bold = True
        elif chunk.startswith("`"):
            run = paragraph.add_run(chunk[1:-1]); run.font.name = "Consolas"; run.font.size = Pt(9)
        else:
            paragraph.add_run(chunk)


def render(source: Path, output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    diagrams = make_diagrams(output.parent)
    doc = Document()
    section = doc.sections[0]
    section.top_margin = section.bottom_margin = Inches(.7)
    section.left_margin = section.right_margin = Inches(.7)
    section.header.paragraphs[0].text = "WOOPLIX  /  ENGINEERING ARCHITECTURE"
    section.header.paragraphs[0].style = doc.styles["Caption"]
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10)
    normal.paragraph_format.space_after = Pt(6)
    normal.paragraph_format.line_spacing = 1.12
    for name in ("Title", "Heading 1", "Heading 2", "Heading 3"):
        doc.styles[name].font.color.rgb = RGBColor.from_string("163D4A")
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run("Wooplix Workflow Studio  •  ")
    field = OxmlElement("w:fldSimple"); field.set(qn("w:instr"), "PAGE"); footer._p.append(field)
    doc.add_heading("Wooplix\nWorkflow Studio", 0)
    doc.add_paragraph("Graph architecture, implementation guide, dependencies and limitations", "Subtitle")
    doc.add_paragraph(f"Prepared {date.today().strftime('%d %B %Y')} | Internal engineering handoff")
    doc.add_paragraph("Purpose: make the team's AI delivery work visible, inspectable and recoverable through a graph-based workspace.")
    doc.add_heading("Read this first", 1)
    doc.add_paragraph("This guide describes the local implementation and the remaining production roadmap. New live-flow changes require publishing/deployment before they appear on the public site. Offline and browser verification do not prove live model quality or unattended production reliability.")
    doc.add_heading("Document map", 1)
    for item in ("Runtime architecture and three workflow diagrams", "Execution, data contracts and recovery behavior", "Agent responsibilities and key dependencies", "Ordered build plan and current limitations", "Local operations, source map and live API contract"):
        doc.add_paragraph(item, "List Bullet")
    doc.add_page_break()
    lines = source.read_text().splitlines()
    i = 0
    figure_index = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith("# "):
            i += 1; continue
        if line.startswith("```"):
            language = line[3:]
            block = []
            i += 1
            while i < len(lines) and not lines[i].startswith("```"):
                block.append(lines[i]); i += 1
            if language == "mermaid":
                doc.add_picture(str(diagrams[figure_index]), width=Inches(6.9))
                doc.add_paragraph(f"Figure {figure_index+1}. {'Runtime components and state flow' if figure_index == 0 else 'Parallel delivery branches and human gates'}.", "Caption")
                figure_index += 1
            else:
                for code in block:
                    run = doc.add_paragraph().add_run(code)
                    run.font.name, run.font.size = "Consolas", Pt(8)
        elif line.startswith("## "):
            title = line[3:]
            if title in {"Current system architecture", "Delivery dependency graph", "Nested proposal / BRD graph", "Agent responsibilities and dependencies", "Key technology and service dependencies", "What to build next (ordered roadmap)", "Current limitations", "Live API contract"}:
                doc.add_page_break()
            doc.add_heading(title, 1)
            if title == "Nested proposal / BRD graph":
                doc.add_picture(str(diagrams[2]), width=Inches(6.9))
                doc.add_paragraph("Figure 3. Source-grounded document specialists and the bounded quality loop.", "Caption")
        elif line.startswith("### "):
            doc.add_heading(line[4:], 2)
        elif line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [cell.strip() for cell in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r"[- :]+", c) for c in cells): rows.append(cells)
                i += 1
            table = doc.add_table(rows=1, cols=len(rows[0]));table.style = "Light Shading Accent 1"
            for index, cells in enumerate(rows):
                row = table.rows[0] if index == 0 else table.add_row()
                for cell, text in zip(row.cells, cells):
                    add_text(cell.paragraphs[0], text)
                if index == 0:
                    repeat = OxmlElement("w:tblHeader");row._tr.get_or_add_trPr().append(repeat)
                    for cell in row.cells:
                        for run in cell.paragraphs[0].runs: run.bold = True
            continue
        else:
            text = line
            # Join soft-wrapped Markdown prose; retain bullets and numbered steps.
            while i + 1 < len(lines) and lines[i+1].strip() and not re.match(r"^(#|\||```|- |\d+\. )", lines[i+1].strip()):
                i += 1; text += " " + lines[i].strip()
            style = "List Bullet" if text.startswith("- ") else "List Number" if re.match(r"^\d+\. ", text) else None
            if style: text = re.sub(r"^(- |\d+\. )", "", text)
            add_text(doc.add_paragraph(style=style), text)
        i += 1
    screenshot = output.parent / "workflow-studio.png"
    if screenshot.exists():
        doc.add_page_break();doc.add_heading("Appendix: implemented workflow canvas", 1)
        doc.add_picture(str(screenshot), width=Inches(6.9))
        doc.add_paragraph("Local browser verification with synthetic data and template agents. The green nodes are saved checkpoints; skipped review nodes are not human approvals.", "Caption")
    doc.core_properties.title = "Wooplix Workflow Studio — Architecture and Build Guide"
    doc.core_properties.author = "Wooplix Engineering"
    doc.save(output)
    print(f"Created {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "out" / "Wooplix-Workflow-Architecture.docx")
    args = parser.parse_args()
    render(ROOT / "docs" / "WORKFLOW_ARCHITECTURE.md", args.output)
