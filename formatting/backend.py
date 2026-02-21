import base64
import os
import tempfile

from docx import Document
from docx.shared import Inches

from utils.docx_to_images import docx_to_page_images, docx_to_page_images_base64, ocr_page_images
from utils.formatter import (
    clear_document_body,
    force_legal_run_format_document,
    force_single_column,
    inject_blocks,
    remove_trailing_empty_and_noise,
)
from utils.llm_formatter import format_text_with_llm
from utils.style_extractor import (
    _paragraph_has_bottom_border,
    extract_document_blueprint,
    extract_styles,
    load_extracted_styles,
    save_document_blueprint,
    save_extracted_styles,
)

# Summons-style page margins (generous, like formal legal documents)
DEFAULT_TOP_MARGIN_IN = 1.25
DEFAULT_BOTTOM_MARGIN_IN = 1.25
DEFAULT_LEFT_MARGIN_IN = 1.25
DEFAULT_RIGHT_MARGIN_IN = 1.25


def _apply_default_margins(doc):
    """Ensure every section has at least default wide margins (proper spacing from page edges)."""
    try:
        for section in doc.sections:
            section.top_margin = Inches(DEFAULT_TOP_MARGIN_IN)
            section.bottom_margin = Inches(DEFAULT_BOTTOM_MARGIN_IN)
            section.left_margin = Inches(DEFAULT_LEFT_MARGIN_IN)
            section.right_margin = Inches(DEFAULT_RIGHT_MARGIN_IN)
    except Exception:
        pass


def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


def get_document_preview_text(docx_path: str) -> str:
    """Build a plain-text preview of the formatted DOCX for display before download.
    Paragraphs with only a bottom border (section underlines) are emitted as [SECTION_UNDERLINE]."""
    doc = Document(docx_path)
    lines = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text and _paragraph_has_bottom_border(para):
            lines.append("[SECTION_UNDERLINE]")
        else:
            lines.append(text if text else "")
    return "\n\n".join(lines).strip()


def extract_and_store_styles(template_file) -> dict:
    """Extract styles from the uploaded DOCX and save to JSON. Returns the style schema."""
    doc = Document(template_file)
    schema = extract_styles(doc)
    save_extracted_styles(schema, base_dir=_project_dir())
    blueprint = extract_document_blueprint(doc)
    save_document_blueprint(blueprint, base_dir=_project_dir())
    return schema


def process_document(generated_text, template_file):
    """
    Input 1: Uploaded DOCX template (desired styles and formatting).
    Input 2: Raw legal text (unformatted).
    Segment and render entire text using template styles (no slot-fill).
    Uses LLM to segment and label; template page images (and OCR text) are generated and sent to the LLM when available.
    """
    project_dir = _project_dir()
    doc = Document(template_file)
    _apply_default_margins(doc)

    schema = extract_styles(doc)
    save_extracted_styles(schema, base_dir=project_dir)

    # Always generate template page images so the LLM can use them when the LLM path is used
    try:
        template_file.seek(0)
        data = template_file.read()
        template_file.seek(0)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp.write(data)
            tmp.flush()
            template_path = tmp.name
        doc_for_images = Document(template_path)
        force_single_column(doc_for_images)
        fd, single_column_path = tempfile.mkstemp(suffix=".docx")
        os.close(fd)
        doc_for_images.save(single_column_path)
        page_bytes = docx_to_page_images(single_column_path, dpi=150, max_pages=15)
        if page_bytes:
            schema["template_page_images"] = [base64.b64encode(b).decode("ascii") for b in page_bytes]
            template_page_ocr_texts = ocr_page_images(page_bytes)
            if template_page_ocr_texts and any(t.strip() for t in template_page_ocr_texts):
                schema["template_page_ocr_texts"] = template_page_ocr_texts
        for path in (single_column_path, template_path):
            if path and os.path.isfile(path):
                try:
                    os.unlink(path)
                except OSError:
                    pass
    except Exception:
        pass

    blocks = []
    try:
        blocks = format_text_with_llm(
            generated_text,
            schema,
            use_slot_fill=False,
            template_page_images=schema.get("template_page_images") or [],
            template_page_ocr_texts=schema.get("template_page_ocr_texts") or [],
        )
    except Exception:
        pass

    clear_document_body(doc)
    # Preserve template's section/column layout (do not force single-column so two-column claimant/attorney blocks match template)
    inject_blocks(
        doc,
        blocks,
        style_map=schema["style_map"],
        style_formatting=schema.get("style_formatting", {}),
        line_samples=schema.get("line_samples", []),
        section_heading_samples=schema.get("section_heading_samples", []),
        template_structure=None,
        numbered_num_id=schema.get("numbered_num_id"),
        numbered_ilvl=schema.get("numbered_ilvl", 0),
        bold_phrases_from_template=schema.get("bold_phrases_from_template"),
        caption_table_layout=schema.get("caption_table_layout"),
    )
    force_legal_run_format_document(doc)
    remove_trailing_empty_and_noise(doc)

    output_path = os.path.join(project_dir, "output", "formatted_output.docx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    preview_text = get_document_preview_text(output_path)
    return output_path, preview_text