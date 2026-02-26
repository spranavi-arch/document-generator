"""
Streamlit app for document generation.

Two flows:
- Template flow: Build template from sample (placeholders) → Fill with JSON.
- Layout-Aware flow: Sample DOCX = layout, frontend text = content → Extract (LLM) → Inject.
"""

import json
import os
import streamlit as st

from backend import (
    build_template_from_sample,
    fill_template_from_json,
    generate_json_from_case_facts,
    get_document_preview_text,
    extract_from_frontend_text,
    inject_into_layout,
    get_layout_structure,
    _call_llm,
    build_auto_template_from_sample,
)
from utils.auto_template_builder import TemplateValidationError
from utils.schema import SUMMONS_SCHEMA_SPEC, validate_summons_data
from utils.template_debug import debug_placeholders_report

st.title("Legal Document Formatter")
st.write(
    "**Template flow:** Build template from sample (placeholders), then fill with JSON. "
    "**Layout-Aware flow:** Sample DOCX defines layout; paste frontend text → extract → inject (no placeholders). "
    "**Auto Template:** Upload sample → LLM detects dynamic fields → replace with «FIELD_NAME» → download template."
)

# ========== Auto Template Builder ==========
st.subheader("Auto Template Builder")
st.caption("Upload a sample DOCX. LLM identifies dynamic values; they are replaced with «FIELD_NAME» placeholders. Body, tables, headers, and footers are included. Validation fails if any original value remains.")
auto_sample = st.file_uploader("Sample DOCX (for auto template)", type=["docx"], key="auto_template_sample")
if auto_sample and st.button("Build auto template", key="build_auto_template"):
    with st.spinner("Extracting text, calling LLM, replacing…"):
        try:
            out_path = build_auto_template_from_sample(auto_sample, llm_callable=_call_llm, output_filename="auto_generated_template.docx")
            st.session_state["auto_template_path"] = out_path
            st.success("Template saved. Download below.")
        except TemplateValidationError as e:
            st.error(str(e))
            st.write("**Values still in document:**")
            for name, val in e.remaining:
                st.code(f"{name}: {val!r}")
        except Exception as e:
            st.error(str(e))

if st.session_state.get("auto_template_path") and os.path.isfile(st.session_state["auto_template_path"]):
    with open(st.session_state["auto_template_path"], "rb") as f:
        docx_bytes = f.read()
    st.download_button(
        "Download auto_generated_template.docx",
        data=docx_bytes,
        file_name="auto_generated_template.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="download_auto_template",
    )

# ========== Layout-Aware flow ==========
st.subheader("Layout-Aware flow (dynamic mapping)")
st.caption("Uploaded sample defines layout. Frontend text defines content. Allegation count adapts automatically.")
layout_sample = st.file_uploader("Sample DOCX (defines layout)", type=["docx"], key="layout_sample")
if layout_sample:
    # Persist to output dir so we can pass path to injector
    project_dir = os.path.dirname(os.path.abspath(__file__))
    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    layout_path = os.path.join(output_dir, "sample_layout.docx")
    with open(layout_path, "wb") as f:
        f.write(layout_sample.getvalue())
    st.session_state["layout_sample_path"] = layout_path
    with st.expander("Inspect layout structure", expanded=False):
        try:
            struct = get_layout_structure(layout_path)
            st.json(struct)
        except Exception as e:
            st.error(str(e))

frontend_text = st.text_area(
    "Frontend text (raw content to extract from)",
    height=140,
    key="frontend_text",
    placeholder="Paste case narrative, caption text, allegations, signature block...",
)
col_a, col_b = st.columns(2)
with col_a:
    if st.button("Extract with LLM"):
        if not frontend_text or not frontend_text.strip():
            st.warning("Enter frontend text first.")
        else:
            with st.spinner("Extracting…"):
                try:
                    data = extract_from_frontend_text(frontend_text.strip(), llm_callable=_call_llm)
                    st.session_state["layout_extracted_json"] = json.dumps(data, indent=2)
                    st.success("Extracted. Click Inject into layout.")
                except Exception as e:
                    st.error(str(e))
with col_b:
    if st.session_state.get("layout_sample_path") and os.path.isfile(st.session_state["layout_sample_path"]):
        layout_json = st.session_state.get("layout_json_edit") or st.session_state.get("layout_extracted_json", "")
        if st.button("Inject into layout"):
            if not layout_json or not layout_json.strip():
                st.warning("Extract with LLM first, or paste JSON above.")
            else:
                try:
                    data = json.loads(layout_json.strip())
                    out_path = inject_into_layout(
                        st.session_state["layout_sample_path"],
                        data,
                        output_filename="layout_filled_output.docx",
                    )
                    st.session_state["layout_output_path"] = out_path
                    st.success("Done. Download below.")
                except json.JSONDecodeError as e:
                    st.error(f"Invalid JSON: {e}")
                except Exception as e:
                    st.error(str(e))

if st.session_state.get("layout_extracted_json"):
    st.text_area("Extracted JSON (edit if needed)", st.session_state["layout_extracted_json"], height=180, key="layout_json_edit")
    if st.button("Apply edited JSON to session"):
        st.session_state["layout_extracted_json"] = st.session_state.get("layout_json_edit", "")

if st.session_state.get("layout_output_path") and os.path.isfile(st.session_state["layout_output_path"]):
    st.download_button(
        "Download layout_filled_output.docx",
        data=open(st.session_state["layout_output_path"], "rb").read(),
        file_name="layout_filled_output.docx",
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        key="download_layout_docx",
    )

# st.divider()

# # ========== Template flow ==========
# st.subheader("1. Build template from sample")
# sample_file = st.file_uploader("Sample Summons & Complaint (DOCX)", type=["docx"], key="sample")

# if sample_file and st.button("Build template"):
#     with st.spinner("Converting sample to template…"):
#         try:
#             template_path, schema_path = build_template_from_sample(sample_file)
#             st.session_state["template_path"] = template_path
#             st.session_state["schema_path"] = schema_path
#             st.success("Template saved. Placeholders inserted (Phase 1).")
#         except Exception as e:
#             st.error(str(e))

# # --- Show schema ---
# if st.session_state.get("schema_path") and os.path.isfile(st.session_state.get("schema_path", "")):
#     with st.expander("Schema (placeholders)"):
#         st.json({k: v for k, v in SUMMONS_SCHEMA_SPEC.items()})

# # --- Validate template (debug placeholders) ---
# if st.session_state.get("template_path") and os.path.isfile(st.session_state.get("template_path", "")):
#     with st.expander("Validate template (debug placeholders)", expanded=False):
#         try:
#             report = debug_placeholders_report(st.session_state["template_path"])
#             st.text(report)
#         except Exception as e:
#             st.error(str(e))

# # --- Phase 3 + 4: Fill template with JSON ---
# st.subheader("2. Fill template with JSON")
# template_path = st.session_state.get("template_path")
# schema_path = st.session_state.get("schema_path")

# if template_path and schema_path and os.path.isfile(template_path):
#     # Option A: Generate JSON from case facts (Phase 3 — LLM)
#     with st.expander("Generate JSON from case facts (LLM)", expanded=False):
#         case_facts = st.text_area(
#             "Case facts",
#             height=120,
#             key="case_facts",
#             placeholder="e.g. Plaintiff: John Doe. Defendant: Jane Smith. Index No. 12345. Date filed: 2024-01-15. ...",
#         )
#         if st.button("Generate JSON"):
#             if not case_facts or not case_facts.strip():
#                 st.warning("Enter case facts first.")
#             else:
#                 with st.spinner("Calling LLM…"):
#                     try:
#                         data = generate_json_from_case_facts(case_facts.strip(), llm_callable=_call_llm)
#                         st.session_state["json_input"] = json.dumps(data, indent=2)
#                         st.success("JSON generated. Edit below if needed, then click Fill template.")
#                     except Exception as e:
#                         st.error(str(e))

#     json_input = st.text_area(
#         "JSON (paste or from LLM above)",
#         height=200,
#         placeholder='{"INDEX_NO": "...", "DATE_FILED": "...", "PLAINTIFF_NAME": "...", ...}',
#         key="json_input",
#     )
#     if st.button("Fill template"):
#         if not json_input or not json_input.strip():
#             st.warning("Enter JSON first.")
#         else:
#             try:
#                 data = json.loads(json_input.strip())
#                 validate_summons_data(data)
#                 output_path = fill_template_from_json(template_path, schema_path, data)
#                 st.session_state["filled_output_path"] = output_path
#                 st.session_state["formatted_editor_html"] = get_document_preview_text(output_path)
#                 st.success("Document filled. Download below.")
#             except json.JSONDecodeError as e:
#                 st.error(f"Invalid JSON: {e}")
#             except ValueError as e:
#                 st.error(str(e))
# else:
#     st.caption("Build a template from a sample (step 1) first.")

# # --- Preview and download ---
# if st.session_state.get("filled_output_path") and os.path.isfile(st.session_state["filled_output_path"]):
#     st.subheader("Filled document")
#     output_path = st.session_state["filled_output_path"]
#     display_html = st.session_state.get(
#         "formatted_editor_html",
#         get_document_preview_text(output_path),
#     )
#     st.text(display_html)
#     with open(output_path, "rb") as f:
#         docx_bytes = f.read()
#     st.download_button(
#         "Download final_output.docx",
#         data=docx_bytes,
#         file_name="final_output.docx",
#         mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
#         key="download_docx",
#     )
