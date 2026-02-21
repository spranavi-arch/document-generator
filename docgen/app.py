import os
import sys
import time
import tempfile
import subprocess
from pathlib import Path
from io import BytesIO
import streamlit as st

# -------------------------
# Path setup
# -------------------------
_root = Path(__file__).resolve().parent.parent
_docgen = Path(__file__).resolve().parent
for p in (str(_root), str(_docgen)):
    if p not in sys.path:
        sys.path.insert(0, p)

# -------------------------
# App config
# -------------------------
st.set_page_config(page_title="Document Generator", layout="wide")
st.title("AI Document Generator")

# -------------------------
# Helpers (UNCHANGED)
# -------------------------
def file_to_text(data: bytes, filename: str) -> str:
    if filename and filename.lower().endswith(".docx"):
        from docx import Document
        doc = Document(BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    return data.decode("utf-8", errors="ignore")


def text_to_docx_bytes(text: str) -> bytes:
    """Build a .docx in memory from plain text (paragraphs split on double newline)."""
    from docx import Document
    doc = Document()
    for block in (text or "").split("\n\n"):
        block = block.strip()
        if block:
            doc.add_paragraph(block.replace("\n", " "))
    buf = BytesIO()
    doc.save(buf)
    buf.seek(0)
    return buf.read()

# -------------------------
# Backend (OOP classes)
# -------------------------
from docgen.sectioner import Sectioner
from docgen.extractor import Extractor
from docgen.section_prompt_generator import SectionPromptGenerator
from docgen.field_fetcher import FieldFetcher, _default_question_for_field
from docgen.question_generator import QuestionGenerator
from docgen.section_generator import SectionGenerator
from docgen.assembler import Assembler
from docgen.utils import fill_placeholders_from_context_with_llm

# -------------------------
# Sidebar
# -------------------------
with st.sidebar:
    st.header("Inputs")
    sample1 = st.file_uploader("Sample document 1", type=["txt", "docx"])
    sample2 = st.file_uploader("Sample document 2", type=["txt", "docx"])
    curl_input = st.text_area("CURL command", height=120)
    extra_context = st.text_area("Extra context (optional)", height=100)

# -------------------------
# Fade-in CSS (SAFE)
# -------------------------
st.markdown(
    """
    <style>
    .fade-in {
        animation: fadeIn 0.8s ease-in forwards;
        opacity: 0;
        margin-bottom: 12px;
    }
    @keyframes fadeIn {
        to { opacity: 1; }
    }
    .muted-box {
        background-color: #f9fafb;
        padding: 12px;
        border-radius: 6px;
        color: #374151;
        font-size: 0.9rem;
        user-select: none;
    }
    .progress-panel {
        display: flex;
        flex-direction: column;
        padding: 12px 0;
    }
    .progress-panel .fade-in { margin-bottom: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# -------------------------
# Pipeline
# -------------------------
def run_pipeline():
    if not sample1 or not sample2:
        st.error("Upload both sample documents.")
        return

    try:
        sample1_bytes = sample1.read()
        sample2_bytes = sample2.read()
    except Exception as e:
        st.error(f"Could not read uploaded files: {e}")
        return

    s1 = file_to_text(sample1_bytes, sample1.name or "")
    s2 = file_to_text(sample2_bytes, sample2.name or "")

    if not (s1 or s2):
        st.error("Both documents are empty. Upload non-empty .txt or .docx files.")
        return

    ctx = (extra_context or "").strip()

    # OOP: docgen pipeline components
    sectioner = Sectioner()
    extractor = Extractor()
    section_prompt_generator = SectionPromptGenerator()
    field_fetcher = FieldFetcher()
    question_generator = QuestionGenerator()
    section_generator = SectionGenerator()
    assembler = Assembler()

    # =====================================================
    # STEP 1 — Section identification (fade-in, slow)
    # =====================================================
    st.subheader("Step 1 · Identifying document sections")

    blueprint = sectioner.divide_into_sections(s1, s2)
    sections = blueprint["sections"]

    sec_container = st.container()

    for sec in sections:
        with sec_container:
            st.markdown(
                f"""
                <div class="fade-in">
                    <strong>{sec['name']}</strong><br/>
                    <span style="color:#6b7280">{sec.get('purpose','')}</span>
                </div>
                """,
                unsafe_allow_html=True
            )
        time.sleep(0.9)

    st.success(f"{len(sections)} sections identified.")

    # =====================================================
    # STEP 2 — Extraction + prompts (continuous loading steps, then arbitrators)
    # =====================================================
    st.subheader("Step 2 · Analyzing sample documents")

    step2_status = st.empty()
    step2_status.markdown("**Extracting content from both documents…**")

    extracted = extractor.extract_sections_from_docs(s1, s2, sections)

    prompts = []
    for i, sec in enumerate(sections):
        sec_name = sec["name"]
        step2_status.markdown(f"**{sec_name}** — extracted text")
        time.sleep(0.3)

        p = section_prompt_generator.generate_prompt_and_fields(
            sec_name,
            sec.get("purpose", ""),
            extracted[i] if i < len(extracted) else "",
        )
        prompts.append(p)
        step2_status.markdown(f"**{sec_name}** — prompt generated")
        time.sleep(0.2)

    step2_status.markdown("**All sections done.**")
    time.sleep(0.4)
    step2_status.empty()

    st.success("Extraction and prompts ready.")

    # Arbitrators: one for all extracted text, one for all prompts (default closed)
    with st.expander("**Extracted text (all sections)**", expanded=False):
        for i, sec in enumerate(sections):
            st.markdown(f"### {sec['name']}")
            st.markdown(
                f"<div class='muted-box'>{extracted[i]}</div>",
                unsafe_allow_html=True,
            )
            st.markdown("")
    with st.expander("**Prompts (all sections)**", expanded=False):
        for i, sec in enumerate(sections):
            st.markdown(f"### {sec['name']}")
            st.markdown(
                f"<div class='muted-box'>{prompts[i]['prompt']}</div>",
                unsafe_allow_html=True,
            )
            st.markdown("")

    # =====================================================
    # STEP 3 — Fetch field values via API
    # =====================================================
    all_required = []
    seen = set()
    for i in range(len(sections)):
        for f in prompts[i].get("required_fields", []):
            if f and f not in seen:
                seen.add(f)
                all_required.append(f)

    field_values = {}
    if (curl_input or "").strip():
        st.subheader("Step 3: Fetching data via API")
        if all_required:
            with st.status("Generating questions for each field...", state="running"):
                field_to_question = question_generator.generate_questions_for_fields(all_required)
            st.success(f"Generated {len(field_to_question)} questions.")
            with st.expander("Field → question used for API", expanded=False):
                for f, q in field_to_question.items():
                    st.markdown(f"**{f}** → \"{q}\"")

            first_field = all_required[0]
            first_question = field_to_question.get(first_field) or _default_question_for_field(first_field)
            debug = field_fetcher.call_chat_api_with_question_debug((curl_input or "").strip(), first_question)
            with st.expander("API test (first request)", expanded=False):
                st.caption(f"Question: \"{first_question}\"")
                if debug.get("error"):
                    st.error(debug["error"])
                st.code(f"Status: {debug.get('status_code')} | Response keys: {debug.get('response_keys', [])}")
                if debug.get("extracted_preview"):
                    st.success(f"Extracted: {debug['extracted_preview']}")
                else:
                    st.warning("No text could be extracted from the response. Check that your API returns a body with one of: content, answer, message, text, or choices[0].message.content.")

            status_placeholder = st.empty()
            progress = st.progress(0, text="Preparing...")

            def on_field_start(field_name: str, index: int, total: int):
                status_placeholder.markdown(f"**Fetching field {index} of {total}:** `{field_name}`")
                progress.progress(index / total, text=f"Fetching: {field_name} ({index}/{total})")

            field_values = field_fetcher.fetch_all_fields_via_chat(
                (curl_input or "").strip(), all_required, field_to_question, on_field_start=on_field_start
            )
            status_placeholder.markdown("**Done.** All fields fetched.")
            progress.progress(1.0, text="Done.")

            status_placeholder.markdown("**Fetching case summary…**")
            case_summary = field_fetcher.fetch_case_summary((curl_input or "").strip())
            field_values["case_summary"] = case_summary or ""
            if case_summary:
                st.caption("Case summary received and will be passed to each section.")
            status_placeholder.markdown("**Done.** All fields fetched.")

        with st.expander("Field → value (from API)", expanded=False):
            for f, v in field_values.items():
                vstr = str(v)
                st.markdown(f"**{f}** → {vstr[:200] + '...' if len(vstr) > 200 else vstr or '(empty)'}")
    else:
        st.subheader("Step 3: Field data")
        st.info("No CURL provided. Using extra context only if provided.")

    if ctx:
        field_values["case_summary_or_context"] = ctx
        field_values["extra_context"] = ctx

    # =====================================================
    # STEP 4 — Drafting (text_area, append-only, no headings)
    # =====================================================
    st.subheader("Step 4 · Drafting the document")

    left, right = st.columns([3, 1])

    draft_text = ""
    draft_sections = []  # for Step 5 assemble()
    draft_height = 220  # will grow as content grows
    total = len(sections)
    completed_sections = []

    with left:
        draft_box = st.empty()  # single box, content updated each step
    with right:
        right_placeholder = st.empty()  # progress panel, height matches draft box

    for i, sec in enumerate(sections, start=1):
        section_name = sec["name"]
        req_fields = prompts[i - 1].get("required_fields", [])
        section_field_values = {f: field_values.get(f, "") for f in req_fields}
        if ctx:
            section_field_values["case_summary_or_context"] = ctx
        if field_values.get("case_summary"):
            section_field_values["case_summary"] = field_values["case_summary"]

        section_text = section_generator.generate_section(
            prompts[i - 1]["prompt"],
            section_field_values,
            sample_text=extracted[i - 1] if i - 1 < len(extracted) else "",
            section_name=section_name,
        )

        # ---- Append draft (NO headings)
        draft_text += section_text.strip() + "\n\n"
        draft_sections.append(section_text)

        # ---- Increase height gradually
        draft_height = min(draft_height + 120, 900)

        # ---- Right panel: same height as draft box, progress (fade-in), completed (bold)
        # Inline animation so it runs when placeholder content is replaced
        fade_style = "opacity:0; animation: fadeIn 0.8s ease-in forwards; margin-bottom: 8px;"
        completed_html = "".join(f"<div>✓ {c}</div>" for c in completed_sections)
        right_placeholder.markdown(
            f"""
            <div class="progress-panel" style="min-height:{draft_height}px;">
                <div class="fade-in" style="{fade_style}"><strong>Drafting section {i} of {total}</strong></div>
                <div class="fade-in" style="{fade_style}"><strong>{section_name}</strong></div>
                {completed_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        completed_sections.append(section_name)

        # ---- Left panel: single box
        with left:
            draft_box.text_area(
                label="Draft being generated",
                value=draft_text,
                height=draft_height,
                key=f"draft_live_{i}",
                disabled=True,
            )

        time.sleep(0.4)

    # ---- Final right panel: all sections with ✓ (including last)
    completed_html = "".join(f"<div><strong>✓ {c}</strong></div>" for c in completed_sections)
    right_placeholder.markdown(
        f"""
        <div class="progress-panel" style="min-height:{draft_height}px;">
            <div style="margin-bottom:8px;">Drafting complete.</div>
            {completed_html}
        </div>
        """,
        unsafe_allow_html=True,
    )

    # =====================================================
    # STEP 5 — Formatting + final editor
    # =====================================================
    st.subheader("Step 5 · Polishing and formatting the document")

    final_draft = assembler.assemble(blueprint, draft_sections)
    # Use LLM to fill [placeholders] from case summary / field context; fallback to key lookup if LLM fails
    final_draft = fill_placeholders_from_context_with_llm(final_draft, field_values)
    formatted_docx_bytes = None
    formatting_error = None

    # Use sample 1 as formatting template when it is a DOCX (run in subprocess so formatting's "utils" package is used)
    if sample1 and sample1.name and sample1.name.lower().endswith(".docx") and sample1_bytes:
        with st.status("Applying template formatting (styles & structure from Sample 1)…", state="running"):
            _formatting_dir = _root / "formatting"
            try:
                with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as _draft_f:
                    _draft_f.write(final_draft)
                    _draft_path = _draft_f.name
                with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as _tpl_f:
                    _tpl_f.write(sample1_bytes)
                    _tpl_path = _tpl_f.name
                _env = os.environ.copy()
                _env["PYTHONPATH"] = str(_formatting_dir)
                _code = """
import os, sys
sys.path.insert(0, os.environ['FORMATTING_DIR'])
from backend import process_document
draft_path = os.environ['DRAFT_PATH']
tpl_path = os.environ['TEMPLATE_PATH']
with open(draft_path, 'r', encoding='utf-8') as f:
    draft = f.read()
with open(tpl_path, 'rb') as f:
    out_path, _ = process_document(draft, f)
print(out_path)
"""
                _env["FORMATTING_DIR"] = str(_formatting_dir)
                _env["DRAFT_PATH"] = _draft_path
                _env["TEMPLATE_PATH"] = _tpl_path
                _r = subprocess.run(
                    [sys.executable, "-c", _code],
                    cwd=str(_formatting_dir),
                    env=_env,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                for _p in (_draft_path, _tpl_path):
                    try:
                        os.unlink(_p)
                    except OSError:
                        pass
                if _r.returncode == 0 and _r.stdout:
                    _out_path = _r.stdout.strip()
                    if os.path.isfile(_out_path):
                        with open(_out_path, "rb") as _f:
                            formatted_docx_bytes = _f.read()
                else:
                    formatting_error = _r.stderr or _r.stdout or "Formatting subprocess failed"
            except Exception as _e:
                formatting_error = str(_e)
        if formatting_error:
            st.warning(f"Formatting pipeline failed (using plain draft): {formatting_error}")

    st.subheader("Formatted document")

    st.text_area(
        "Final output",
        value=final_draft,
        height=600,
        label_visibility="collapsed"
    )

    if formatted_docx_bytes:
        st.download_button(
            "Download formatted .docx",
            data=formatted_docx_bytes,
            file_name="formatted_draft.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary"
        )
    else:
        st.download_button(
            "Download .docx",
            data=text_to_docx_bytes(final_draft),
            file_name="draft.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary"
        )

    # Persist results so they stay visible after download or other re-runs
    st.session_state["pipeline_done"] = True
    st.session_state["pipeline_blueprint"] = blueprint
    st.session_state["pipeline_sections"] = sections
    st.session_state["pipeline_extracted"] = extracted
    st.session_state["pipeline_prompts"] = prompts
    st.session_state["pipeline_draft_text"] = draft_text
    st.session_state["pipeline_final_draft"] = final_draft
    st.session_state["pipeline_completed_sections"] = completed_sections
    st.session_state["pipeline_formatted_docx_bytes"] = formatted_docx_bytes  # None or bytes


def render_saved_pipeline_results():
    """Re-render all steps and final output from session state (keeps UI after download)."""
    sections = st.session_state.get("pipeline_sections", [])
    extracted = st.session_state.get("pipeline_extracted", [])
    prompts = st.session_state.get("pipeline_prompts", [])
    draft_text = st.session_state.get("pipeline_draft_text", "")
    final_draft = st.session_state.get("pipeline_final_draft", "")
    completed_sections = st.session_state.get("pipeline_completed_sections", [])
    formatted_docx_bytes = st.session_state.get("pipeline_formatted_docx_bytes")

    if not sections:
        return

    # Step 1
    st.subheader("Step 1 · Identifying document sections")
    for sec in sections:
        st.markdown(
            f"""
            <div class="fade-in">
                <strong>{sec['name']}</strong><br/>
                <span style="color:#6b7280">{sec.get('purpose','')}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )
    st.success(f"{len(sections)} sections identified.")

    # Step 2
    st.subheader("Step 2 · Analyzing sample documents")
    with st.expander("**Extracted text (all sections)**", expanded=False):
        for i, sec in enumerate(sections):
            st.markdown(f"### {sec['name']}")
            if i < len(extracted):
                st.markdown(
                    f"<div class='muted-box'>{extracted[i]}</div>",
                    unsafe_allow_html=True,
                )
            st.markdown("")
    with st.expander("**Prompts (all sections)**", expanded=False):
        for i, sec in enumerate(sections):
            st.markdown(f"### {sec['name']}")
            if i < len(prompts):
                st.markdown(
                    f"<div class='muted-box'>{prompts[i].get('prompt', '')}</div>",
                    unsafe_allow_html=True,
                )
            st.markdown("")
    st.success("Extraction and prompts ready.")

    # Step 4 (summary)
    st.subheader("Step 4 · Drafting the document")
    left, right = st.columns([3, 1])
    with left:
        st.text_area(
            "Draft being generated",
            value=draft_text,
            height=min(220 + 120 * max(0, len(sections) - 1), 900),
            key="saved_draft_display",
            disabled=True,
        )
    with right:
        completed_html = "".join(f"<div><strong>✓ {c}</strong></div>" for c in completed_sections)
        st.markdown(
            f"""
            <div class="progress-panel">
                <div style="margin-bottom:8px;">Drafting complete.</div>
                {completed_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

    # Step 5
    st.subheader("Step 5 · Polishing and formatting the document")
    st.subheader("Formatted document")
    st.text_area(
        "Final output",
        value=final_draft,
        height=600,
        key="saved_final_display",
        label_visibility="collapsed",
    )
    if formatted_docx_bytes:
        st.download_button(
            "Download formatted .docx",
            data=formatted_docx_bytes,
            file_name="formatted_draft.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
            key="download_formatted_saved",
        )
    else:
        st.download_button(
            "Download .docx",
            data=text_to_docx_bytes(final_draft),
            file_name="draft.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            type="primary",
            key="download_plain_saved",
        )


# -------------------------
# Run
# -------------------------
if st.button("Run document generation", type="primary"):
    st.session_state.pop("pipeline_done", None)  # clear so we run fresh
    status = st.empty()
    status.info("Running pipeline…")
    try:
        run_pipeline()
    except Exception as e:
        status.empty()
        st.error("Pipeline failed. See details below.")
        st.exception(e)
    else:
        status.empty()
elif st.session_state.get("pipeline_done"):
    render_saved_pipeline_results()