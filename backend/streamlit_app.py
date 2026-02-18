"""
Streamlit UI for the document generator.
Run from the backend directory: streamlit run streamlit_app.py
Or from project root: streamlit run backend/streamlit_app.py
"""
import json
import sys
from io import BytesIO
from pathlib import Path

# Ensure backend is on path when run from project root
_backend = Path(__file__).resolve().parent
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

import streamlit as st

# Backend imports (after path fix)
from blueprint.generator import BlueprintGenerator
from extractor.section_extractor import SectionExtractor
from promps.prompt_builder import PromptBuilder
from drafts.draft_engine import DraftEngine
from assembler.assembler import Assembler

OLE_MAGIC = b"\xd0\xcf\x11\xe0"


def _save_templates(blueprint: dict, templates: dict) -> None:
    path = Path(__file__).resolve().parent / "storage" / "templates.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"blueprint": blueprint, "templates": templates}, f, indent=2)


def file_to_text(data: bytes, filename: str) -> str:
    """Extract plain text from uploaded file. .txt or .docx only."""
    if data.startswith(OLE_MAGIC):
        raise ValueError("Legacy .doc (binary) is not supported. Please upload .docx or .txt files.")
    name = (filename or "").lower()
    if name.endswith(".docx"):
        from docx import Document
        doc = Document(BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252")


st.set_page_config(page_title="Document Generator", layout="wide")
st.title("Legal Document Generator")

with st.sidebar:
    st.header("Upload samples")
    sample1 = st.file_uploader("Sample document 1", type=["txt", "docx"], key="s1")
    sample2 = st.file_uploader("Sample document 2", type=["txt", "docx"], key="s2")
    st.markdown("---")
    case_summary = st.text_area(
        "Case summary / extracted data",
        height=200,
        placeholder="Paste case facts, parties, dates, claims, and any data to fill into the draft...",
    )

def run_pipeline():
    if not sample1 or not sample2:
        st.error("Please upload both sample documents.")
        return
    if not (case_summary or "").strip():
        st.warning("Case summary is empty. Draft will rely only on sample format.")

    try:
        s1 = file_to_text(sample1.read(), sample1.name or "")
        sample1.seek(0)
        s2 = file_to_text(sample2.read(), sample2.name or "")
    except Exception as e:
        st.error(f"Could not read files: {e}")
        return

    # --- Step 1: Blueprint ---
    st.subheader("Identified sections")
    try:
        blueprint = BlueprintGenerator().generate(s1, s2)
    except ValueError as e:
        st.error(f"Blueprint failed: {e}")
        return

    sections = blueprint.get("sections", [])
    for i, sec in enumerate(sections, 1):
        st.markdown(f"**{i}. {sec.get('name', '?')}** — {sec.get('purpose', '') or '(no purpose)'}")
    st.success(f"Found {len(sections)} section(s).")

    # --- Step 2: Extract templates (section-by-section so UI updates after each LLM call) ---
    st.subheader("Step 2: Extracting section text from samples")
    st.caption("This step can take a few minutes. Progress updates below.")
    from extractor.section_extractor import extract_one_section
    progress = st.progress(0, text="Starting...")
    status_placeholder = st.empty()
    t1 = {}
    t2 = {}
    total_steps = len(sections) * 2  # sample1 + sample2
    step = 0
    try:
        for i, sec in enumerate(sections):
            name = sec["name"]
            if not name:
                continue
            status_placeholder.caption(f"Sample 1 — section {i + 1}/{len(sections)}: **{name}**")
            progress.progress(step / total_steps, text=f"Sample 1: {name}")
            t1[name] = extract_one_section(s1, name)
            step += 1
        for i, sec in enumerate(sections):
            name = sec["name"]
            if not name:
                continue
            status_placeholder.caption(f"Sample 2 — section {i + 1}/{len(sections)}: **{name}**")
            progress.progress(step / total_steps, text=f"Sample 2: {name}")
            t2[name] = extract_one_section(s2, name)
            step += 1
    except Exception as e:
        progress.empty()
        status_placeholder.empty()
        st.error(f"Section extraction failed: {e}")
        return
    progress.progress(1.0, text="Done extracting.")
    status_placeholder.caption("Extraction complete. Building prompts and generating draft...")

    templates = {}
    for sec in sections:
        name = sec["name"]
        part1 = t1.get(name, "")
        part2 = t2.get(name, "")
        templates[name] = (part1 + "\n" + part2).strip() if (part1 or part2) else ""

    _save_templates(blueprint, templates)

    try:
        _run_generation(st, sections, blueprint, templates, case_summary or "")
    except Exception as e:
        st.error(f"Error during prompt build or draft generation: {e}")
        import traceback
        st.code(traceback.format_exc())


def _run_generation(st, sections, blueprint, templates, case_summary):
    """Build prompts and generate draft (separate so we can catch errors)."""
    # --- Step 3: Build prompts (show how each prompt is made) ---
    st.subheader("How the prompt for each section is built")
    st.markdown(
        "For every section we build one prompt from: **section name**, **purpose** (from blueprint), "
        "and **sample text** (from your uploaded docs). That prompt is then sent to the model together "
        "with your **Case summary** so it can fill in the new content in the same format as the sample."
    )
    builder = PromptBuilder()
    prompts = {}
    for sec in sections:
        name = sec["name"]
        purpose = sec.get("purpose", "")
        examples = templates.get(name, "")
        prompts[name] = builder.build(name, purpose, examples)

    with st.expander("View prompt ingredients and full prompt for each section", expanded=False):
        for sec in sections:
            name = sec["name"]
            purpose = sec.get("purpose", "") or "(none)"
            sample_preview = (templates.get(name, "") or "")[:400]
            if len(templates.get(name, "") or "") > 400:
                sample_preview += "\n..."
            st.markdown(f"### {name}")
            st.markdown("**Inputs used to build the prompt:**")
            st.markdown(f"- **Section name:** {name}")
            st.markdown(f"- **Purpose:** {purpose}")
            st.markdown(f"- **Sample text (format to follow):**")
            st.text(sample_preview)
            st.markdown("**Full prompt sent to the model (prompt + Case Data below):**")
            st.text(prompts[name])
            st.markdown("---")

    # --- Step 4: Generate sections one by one; show draft as it grows ---
    st.subheader("Draft")
    st.caption("Updates below as each section is generated. You can scroll and copy from this box.")
    draft_placeholder = st.empty()
    draft_parts = []
    engine = DraftEngine()

    for i, sec in enumerate(sections):
        name = sec["name"]
        with st.status(f"Generating section {i + 1}/{len(sections)}: **{name}**", state="running"):
            text = engine.generate_one_section(prompts[name], case_summary or "")
            draft_parts.append(text.strip())
        current_draft = "\n\n".join(draft_parts)
        draft_placeholder.code(current_draft, language=None)

    # --- Final draft (same content, for copy/download) ---
    sections_dict = {sec["name"]: (draft_parts[j] if j < len(draft_parts) else "") for j, sec in enumerate(sections)}
    final_doc = Assembler().assemble(blueprint, sections_dict)

    st.success("Draft complete.")
    st.subheader("Final draft (copy or download)")
    st.text_area(
        "Copy draft",
        value=final_doc,
        height=420,
        label_visibility="collapsed",
        key="final_draft",
    )


if st.button("Analyze samples & generate draft", type="primary"):
    run_pipeline()
