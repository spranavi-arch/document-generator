"""
Streamlit app for the template workflow only.

Flow: Upload sample → Build template + schema → Fill template with JSON → Download.
"""

import json
import os
import streamlit as st

from backend import (
    build_template_from_sample,
    extract_fields_from_text,
    fill_template_from_json,
    get_document_preview_text,
)
from utils.html_to_docx import plain_text_to_simple_html
from utils.template_debug import debug_placeholders, debug_placeholders_report
from utils.template_filler import build_template_fill_prompt, load_schema


st.title("Legal Document — Template Workflow")
st.write(
    "**Current approach:** Build a reusable template from a sample DOCX, then fill it with JSON. "
    "No style extraction or document rebuild — only placeholder replacement and block merge."
)

# --- Build template from sample ---
st.subheader("1. Build template from sample")
sample_file = st.file_uploader("Primary sample (layout) — DOCX", type=["docx"], key="sample")
secondary_file = st.file_uploader(
    "Secondary sample (optional — for validation & merging placeholders)",
    type=["docx"],
    key="secondary_sample",
)

if sample_file:
    doc_type = st.selectbox("Document type", ["SummonsAndComplaint", "Motion"], key="doc_type")
    if st.button("Build template"):
        with st.spinner("Building template and schema…"):
            try:
                template_path, schema_path, placeholder_keys, validation_info = build_template_from_sample(
                    sample_file, doc_type=doc_type, secondary_file=secondary_file
                )
                st.session_state["template_path"] = template_path
                st.session_state["schema_path"] = schema_path
                st.session_state["placeholder_keys"] = placeholder_keys
                st.session_state["validation_info"] = validation_info
                st.session_state["schema"] = load_schema(schema_path)
                st.success(f"Template and schema saved. {len(placeholder_keys)} placeholders.")
                if validation_info:
                    st.info("Secondary sample used: placeholders merged. See comparison below.")
                    st.caption("Template built with diff-based mining: differing spans between the two samples were classified as placeholders (LLM when configured, else heuristics).")
            except Exception as e:
                st.error(str(e))

# --- Show schema when available ---
if st.session_state.get("schema_path") and os.path.isfile(st.session_state["schema_path"]):
    schema = st.session_state.get("schema") or load_schema(st.session_state["schema_path"])
    with st.expander("Schema (placeholders)"):
        st.json(schema)

# --- Validate template (debug placeholders) before fill ---
if st.session_state.get("template_path") and os.path.isfile(st.session_state["template_path"]):
    with st.expander("Validate template (debug placeholders)", expanded=False):
        st.caption("Paragraphs and table cells containing {{...}} plus run breakdown. Check for truncated static text, duplicated markers, or missing prefixes (e.g. COUNTY OF {{COUNTY}}).")
        try:
            report = debug_placeholders_report(st.session_state["template_path"])
            st.text(report)
        except Exception as e:
            st.error(str(e))

# --- Validation comparison when secondary sample was used ---
if st.session_state.get("validation_info"):
    vi = st.session_state["validation_info"]
    with st.expander("Primary vs secondary (placeholder comparison)", expanded=True):
        st.write("**Merged placeholders** (used in schema):")
        st.code(", ".join(vi["merged"]) if vi["merged"] else "(none)")
        if vi["only_in_primary"]:
            st.warning(f"Only in primary sample: {', '.join(vi['only_in_primary'])}")
        if vi["only_in_secondary"]:
            st.info(f"Added from secondary sample: {', '.join(vi['only_in_secondary'])}")

# --- Fill template with JSON ---
st.subheader("2. Fill template with JSON")
template_path = st.session_state.get("template_path")
schema_path = st.session_state.get("schema_path")

if template_path and schema_path and os.path.isfile(template_path) and os.path.isfile(schema_path):
    # Option A: Extract fields from input text (LLM)
    with st.expander("Extract fields from text (LLM)", expanded=False):
        extract_input = st.text_area(
            "Paste or type text (case narrative, notes, etc.) to extract placeholder values",
            height=120,
            key="extract_input",
            placeholder="e.g. Plaintiff: John Doe. Defendant: Jane Smith. Index No. 12345. Date filed: 2024-01-15. ...",
        )
        if st.button("Extract fields"):
            if not extract_input or not extract_input.strip():
                st.warning("Enter some text first.")
            else:
                with st.spinner("Calling LLM to extract fields…"):
                    try:
                        data = extract_fields_from_text(extract_input.strip(), schema_path)
                        st.session_state["json_input"] = json.dumps(data, indent=2)
                        st.success("Fields extracted. Edit JSON below if needed, then click Fill template.")
                    except Exception as e:
                        st.error(str(e))

    json_input = st.text_area(
        "Paste JSON that matches the schema (or case facts for LLM-assisted fill)",
        height=200,
        placeholder='{"INDEX_NO": "...", "DATE_FILED": "...", "PLAINTIFF_NAME": "...", ...}',
        key="json_input",
    )
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Fill template"):
            if not json_input or not json_input.strip():
                st.warning("Enter JSON first.")
            else:
                with st.spinner("Filling template…"):
                    try:
                        schema = load_schema(schema_path)
                        # If it looks like raw text (case facts), try LLM prompt helper message
                        text = json_input.strip()
                        if text.startswith("{") and ("}" in text or "\n" in text):
                            data = json.loads(text)
                        else:
                            # User might have pasted case facts; try to parse as JSON anyway, else show hint
                            try:
                                data = json.loads(text)
                            except json.JSONDecodeError:
                                st.info(
                                    "Input is not valid JSON. Paste a JSON object that matches the schema, "
                                    "or use an LLM with the prompt from the expander below to generate JSON from case facts."
                                )
                                data = None
                        if data is not None:
                            output_path = fill_template_from_json(
                                template_path, schema_path, data
                            )
                            st.session_state["filled_output_path"] = output_path
                            st.session_state["formatted_editor_html"] = plain_text_to_simple_html(
                                get_document_preview_text(output_path)
                            )
                            st.success("Document filled. Download below.")
                    except Exception as e:
                        st.error(str(e))
    with col2:
        if schema_path and os.path.isfile(schema_path):
            with st.expander("Prompt for LLM (schema + case facts → JSON)"):
                facts = st.text_area("Case facts", height=80, key="case_facts")
                if facts:
                    prompt = build_template_fill_prompt(
                        load_schema(schema_path), facts
                    )
                    st.text_area("Copy this prompt", prompt, height=200, key="prompt_copy")
else:
    st.caption("Build a template from a sample (step 1) first.")

# --- Preview and download filled document ---
if st.session_state.get("filled_output_path") and os.path.isfile(
    st.session_state["filled_output_path"]
):
    st.subheader("Filled document")
    output_path = st.session_state["filled_output_path"]
    display_html = st.session_state.get(
        "formatted_editor_html",
        plain_text_to_simple_html(get_document_preview_text(output_path)),
    )
    doc_html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
html, body {{ background: #fff; }}
body {{ font-family: "Times New Roman", Georgia, serif; font-size: 12pt; line-height: 1.4; max-width: 7in; margin: 1em auto; padding: 0 1em; }}
p {{ margin: 0.4em 0; }}
</style>
</head>
<body>
{display_html}
</body>
</html>"""
    st.components.v1.html(doc_html, height=500, scrolling=True)
    with open(output_path, "rb") as f:
        docx_bytes = f.read()
    st.download_button(
        "Download filled document (.docx)",
        data=docx_bytes,
        file_name="filled_output.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="download_docx",
    )
