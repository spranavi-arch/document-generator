from dotenv import load_dotenv
load_dotenv()

import os
import re
import streamlit as st
from backend import extract_and_store_styles, process_document
from utils.style_extractor import load_document_blueprint
from utils.html_to_docx import html_to_docx_bytes, plain_text_to_simple_html, simple_html_to_plain_text

try:
    from streamlit_quill import st_quill
    HAS_QUILL = True
except ImportError:
    HAS_QUILL = False

try:
    from streamlit_lexical import streamlit_lexical
    HAS_LEXICAL = True
except ImportError:
    HAS_LEXICAL = False

def normalize_editor_html(html: str) -> str:
    """Normalize editor HTML so double breaks become paragraph boundaries (fixes 'everything becomes one paragraph')."""
    if not html:
        return "<p><br></p>"
    html = re.sub(r"(<br\s*/?>\s*){2,}", "</p><p>", html, flags=re.I)
    if "<p" not in html.lower():
        html = "<p>" + html + "</p>"
    return html


def _markdown_to_html(md: str) -> str:
    """Convert markdown to HTML for DOCX pipeline (requires markdown package)."""
    if not md or not md.strip():
        return "<p><br></p>"
    try:
        import markdown
        return markdown.markdown(md, extensions=["nl2br"])
    except Exception:
        return plain_text_to_simple_html(md)


def add_space_paragraph(html: str) -> str:
    """Append a blank paragraph for padding/space."""
    if not html or not html.strip():
        return "<p><br></p><p>&nbsp;</p>"
    html = html.rstrip()
    if not html.endswith("</p>"):
        html += "</p>"
    return html + "<p>&nbsp;</p>"


st.title("Legal Document Formatter")
st.write(
    "Upload a DOCX template to extract its styles. When you click Format, each page of the template is converted to an image "
    "and sent to the LLM as the primary reference for layout, spacing, and structure. Enter your text and click Format to get a "
    "document that matches the template's formatting."
)

template_file = st.file_uploader("Upload the DOCX template", type=["docx"])

if template_file:
    schema = extract_and_store_styles(template_file)
    num_styles = len(schema.get("paragraph_style_names", []))
    num_tables = len(schema.get("tables", []))
    msg = f"Styles and tables extracted: {num_styles} paragraph styles"
    if num_tables:
        msg += f", {num_tables} table(s)"
    msg += "."
    st.success(msg)
    guide = schema.get("style_guide") or schema.get("style_guide_markdown")
    if guide:
        with st.expander("Style guide — applied to input text"):
            st.text(guide)
    if schema.get("style_map"):
        with st.expander("Style mapping (block type → style name)"):
            st.json(schema["style_map"])
    tables = schema.get("tables") or []
    if tables:
        with st.expander("Extracted tables"):
            for t in tables:
                idx = t.get("table_index", 0)
                rows, cols = t.get("rows", 0), t.get("cols", 0)
                st.markdown(f"**Table {idx}**: {rows} × {cols}")
                preview = t.get("cell_preview") or []
                if preview:
                    for ri, row_cells in enumerate(preview):
                        for ci, cell_text in enumerate(row_cells):
                            if cell_text:
                                st.caption(f"  Row {ri}, Col {ci}: {cell_text[:80]}{'…' if len(cell_text) > 80 else ''}")
                    st.markdown("---")
    blueprint = load_document_blueprint()
    if blueprint:
        with st.expander("Document blueprint (formatting metadata)"):
            st.caption("Complete style and layout schema for programmatic application. Saved to output/document_blueprint.json")
            st.json(blueprint)

generated_text = st.text_area("Enter the generated text", height=300)

if generated_text and template_file:
    if st.button("Format with LLM"):
        with st.spinner("Calling LLM and building document…"):
            try:
                output_path, preview_text = process_document(generated_text, template_file)
                st.session_state["formatted_output_path"] = output_path
                st.session_state["formatted_editor"] = preview_text
                st.session_state["formatted_editor_html"] = plain_text_to_simple_html(preview_text)
                st.success("Document formatted successfully. Edit below with alignment and formatting, then download.")
            except Exception as e:
                st.error(str(e))

if st.session_state.get("formatted_output_path") or st.session_state.get("formatted_editor_html"):
    st.subheader("Editor")
    initial_html = st.session_state.get("formatted_editor_html", "<p><br></p>")

    # Single editor: Quill if available, else Lexical, else plain text
    if HAS_QUILL:
        _col, _cap = st.columns([1, 5])
        with _col:
            if st.button("Add space", key="add_space_quill", help="Append a blank paragraph."):
                current = st.session_state.get("formatted_editor_html") or initial_html
                st.session_state["formatted_editor_html"] = add_space_paragraph(current)
                try:
                    st.rerun()
                except AttributeError:
                    st.experimental_rerun()
        with _cap:
            st.caption("Use the toolbar for bold, italic, underline, alignment, lists.")
        editor_content = st_quill(
            value=initial_html,
            html=True,
            key="formatted_editor",
            toolbar=[
                ["bold", "italic", "underline", "strike"],
                [{"align": []}],
                [{"list": "ordered"}, {"list": "bullet"}],
                ["clean"],
            ],
        )
        if editor_content is not None:
            st.session_state["formatted_editor_html"] = editor_content
        editor_html = st.session_state.get("formatted_editor_html") or initial_html
    elif HAS_LEXICAL:
        _col, _cap = st.columns([1, 5])
        with _col:
            if st.button("Add space", key="add_space_lexical", help="Append a blank paragraph."):
                current = st.session_state.get("formatted_editor_html") or initial_html
                st.session_state["formatted_editor_html"] = add_space_paragraph(current)
                try:
                    st.rerun()
                except AttributeError:
                    st.experimental_rerun()
        with _cap:
            st.caption("Use **bold**, *italic*, lists. Output is converted to DOCX.")
        initial_value = simple_html_to_plain_text(initial_html)
        md_content = streamlit_lexical(
            value=initial_value,
            placeholder="Edit document (markdown supported)",
            height=400,
            debounce=500,
            key="formatted_editor_lexical",
        )
        if md_content is not None:
            st.session_state["formatted_editor_html"] = _markdown_to_html(md_content)
        editor_html = st.session_state.get("formatted_editor_html") or _markdown_to_html(initial_value)
    else:
        _col, _cap = st.columns([1, 5])
        with _col:
            if st.button("Add space", key="add_space_plain", help="Append a blank paragraph."):
                current = st.session_state.get("formatted_editor_html") or initial_html
                st.session_state["formatted_editor_html"] = add_space_paragraph(current)
                try:
                    st.rerun()
                except AttributeError:
                    st.experimental_rerun()
        with _cap:
            st.caption("Install streamlit-quill or streamlit-lexical for rich editing.")
        editor_content = st.text_area(
            "Edit formatted document",
            value=simple_html_to_plain_text(initial_html),
            height=400,
            key="formatted_editor_plain",
        )
        if editor_content is not None:
            st.session_state["formatted_editor_html"] = plain_text_to_simple_html(editor_content)
        editor_html = st.session_state.get("formatted_editor_html") or initial_html

    try:
        # Use the actual formatted DOCX from "Format with LLM" so alignment/italic/column fixes are preserved
        output_path = st.session_state.get("formatted_output_path")
        if output_path and os.path.isfile(output_path):
            with open(output_path, "rb") as f:
                docx_bytes = f.read()
        else:
            editor_html = st.session_state.get("formatted_editor_html") or initial_html
            editor_html_norm = normalize_editor_html(editor_html)
            docx_bytes = html_to_docx_bytes(editor_html_norm)
        if not docx_bytes or len(docx_bytes) == 0:
            st.warning("Document is empty. Format with LLM first, or add content in the editor above, then download.")
        else:
            clicked = st.download_button(
                "Download document (.docx)",
                data=docx_bytes,
                file_name="formatted_output.docx",
                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                key="download_docx",
            )
            if clicked:
                st.caption("Download started — check your browser’s downloads folder if the file didn’t open.")
        if output_path and os.path.isfile(output_path):
            st.caption("Download uses the formatted document (template styles, alignment, single column). Editor changes are not included.")
    except Exception as e:
        st.error(f"Could not build DOCX: {e}")
