"""
Formatting backend: apply DOCX template styles and structure to raw text.
Input: (1) raw legal text (e.g. final draft from any source), (2) DOCX template.
Output: formatted DOCX matching the template's styles and layout.

This module is separate from the docgen pipeline. It does not use docgen prompts or
section logic; docgen handles document generation and section structure; formatting
only applies template-based styling to the finished text.
"""
import base64
import copy
import os
import tempfile
from io import BytesIO

from docx import Document
from docx.shared import Inches

from utils.docx_to_images import docx_to_page_images, docx_to_page_images_base64, ocr_page_images
from utils.formatter import (
    clear_document_body,
    force_single_column,
    inject_blocks,
    inject_blocks_using_paragraph_sources,
    remove_trailing_empty_and_noise,
)
from utils.llm_formatter import (
    build_formatting_prompt_preview,
    format_section_with_instruction,
    format_text_with_llm,
)
from utils.style_extractor import (
    _paragraph_has_bottom_border,
    extract_document_blueprint,
    extract_paragraph_format_sources,
    extract_styles,
    load_extracted_styles,
    save_document_blueprint,
    save_extracted_styles,
)
from utils.structural_pipeline import (
    build_document_from_structural_output,
    run_structural_formatting,
    validate_against_sample,
)
from utils.draft_driven_formatter import (
    build_document_from_draft,
    parse_draft_into_blocks,
)

# Summons-style page margins (generous, like formal legal documents)
DEFAULT_TOP_MARGIN_IN = 1.25
DEFAULT_BOTTOM_MARGIN_IN = 1.25
DEFAULT_LEFT_MARGIN_IN = 1.25
DEFAULT_RIGHT_MARGIN_IN = 1.25



def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


def _ensure_seekable_stream(template_file):
    """Return a seekable file-like object (BytesIO). Handles bytes, file-like, or Package."""
    if template_file is None:
        raise ValueError("template_file is required")
    if isinstance(template_file, bytes):
        return BytesIO(template_file)
    if getattr(template_file, "seek", None) is not None and getattr(template_file, "read", None) is not None:
        try:
            template_file.seek(0)
            return template_file
        except (AttributeError, OSError):
            pass
    # python-docx Package (or Document) has .save(stream)
    if getattr(template_file, "save", None) is not None:
        buf = BytesIO()
        template_file.save(buf)
        buf.seek(0)
        return buf
    raise TypeError(
        "template_file must be bytes, a file-like object with seek()/read(), or a Document/Package"
    )


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


def get_schema_from_template(template_file) -> dict:
    """Extract style schema from template DOCX (template_content, style_guide, etc.) without saving. For step 5a."""
    template_file = _ensure_seekable_stream(template_file)
    template_file.seek(0)
    doc = Document(template_file)
    return extract_styles(doc)


def process_document(generated_text, template_file):
    """
    Input 1: Uploaded DOCX template (desired styles and formatting).
    Input 2: Raw legal text (unformatted).
    Segment and render entire text using template styles (no slot-fill).
    Template is also converted to page images and sent to the LLM when possible (vision).
    """
    project_dir = _project_dir()
    template_file.seek(0)
    doc = Document(template_file)
    # Extract exact format of each template paragraph (before any clearing) for paragraph-clone approach
    paragraph_sources = extract_paragraph_format_sources(doc)

    schema = extract_styles(doc)
    save_extracted_styles(schema, base_dir=project_dir)

    # Convert document to images (each page → image), then send to LLM for formatting reference.
    # Template may have multi-column layout; convert a single-column copy so each page image is one column (not 3 side-by-side).
    template_path = None
    single_column_path = None
    template_page_images = []
    template_page_ocr_texts = []
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
            template_page_images = [base64.b64encode(b).decode("ascii") for b in page_bytes]
            schema["template_page_images"] = template_page_images
            template_page_ocr_texts = ocr_page_images(page_bytes)
            if template_page_ocr_texts and any(t.strip() for t in template_page_ocr_texts):
                schema["template_page_ocr_texts"] = template_page_ocr_texts
    except Exception:
        pass
    for path in (single_column_path, template_path):
        if path and os.path.isfile(path):
            try:
                os.unlink(path)
            except OSError:
                pass

    prompt_preview = build_formatting_prompt_preview(
        generated_text,
        schema,
        template_page_images=template_page_images,
        template_page_ocr_texts=template_page_ocr_texts if template_page_ocr_texts else None,
    )

    blocks = format_text_with_llm(
        generated_text,
        schema,
        use_slot_fill=False,
        template_page_images=template_page_images,
        template_page_ocr_texts=template_page_ocr_texts if template_page_ocr_texts else None,
    )

    clear_document_body(doc)
    force_single_column(doc)
    # Use paragraph-clone approach: copy exact format from each template paragraph by index (spacing, color, font preserved)
    if paragraph_sources:
        inject_blocks_using_paragraph_sources(doc, blocks, paragraph_sources)
    else:
        inject_blocks(
            doc,
            blocks,
            style_map=schema["style_map"],
            style_formatting=schema.get("style_formatting", {}),
            line_samples=schema.get("line_samples", []),
            section_heading_samples=schema.get("section_heading_samples", []),
            template_structure=None,
        )
    remove_trailing_empty_and_noise(doc)

    output_path = os.path.join(project_dir, "output", "formatted_output.docx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    preview_text = get_document_preview_text(output_path)
    return output_path, preview_text, prompt_preview


def process_document_section_by_section(
    section_texts: list,
    section_formatting_prompts: list,
    template_file,
):
    """
    Step 5b: Format the document section by section using per-section formatting instructions.
    section_texts: list of generated section text (same order as sections).
    section_formatting_prompts: list of formatting instructions (one per section, from step 5a).
    template_file: the sample DOCX (file-like or path) for styles and layout.
    Returns (output_path, preview_text).
    """
    template_file.seek(0)
    doc = Document(template_file)
    paragraph_sources = extract_paragraph_format_sources(doc)
    schema = extract_styles(doc)
    template_file.seek(0)

    all_blocks = []
    n = max(len(section_texts), len(section_formatting_prompts))
    for i in range(n):
        text = section_texts[i] if i < len(section_texts) else ""
        instruction = section_formatting_prompts[i] if i < len(section_formatting_prompts) else ""
        if not text and not instruction:
            continue
        blocks = format_section_with_instruction(text, instruction, schema)
        all_blocks.extend(blocks)

    clear_document_body(doc)
    force_single_column(doc)
    if paragraph_sources:
        inject_blocks_using_paragraph_sources(doc, all_blocks, paragraph_sources)
    else:
        inject_blocks(
            doc,
            all_blocks,
            style_map=schema["style_map"],
            style_formatting=schema.get("style_formatting", {}),
            line_samples=schema.get("line_samples", []),
            section_heading_samples=schema.get("section_heading_samples", []),
            template_structure=None,
        )
    remove_trailing_empty_and_noise(doc)

    project_dir = _project_dir()
    output_path = os.path.join(project_dir, "output", "formatted_output_section_by_section.docx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    preview_text = get_document_preview_text(output_path)
    return output_path, preview_text, all_blocks


def process_document_draft_driven(final_draft_text: str, template_file) -> tuple[str, str]:
    """
    Draft-driven formatting: structure from the final draft, styling from the sample.
    - Parse the draft into blocks (heading, body, numbered, line, etc.) by its own structure.
    - Use the sample DOCX only as a style guide (which style for each role + formatting).
    - Build output with one paragraph per draft block, applying the sample's style for that role.
    Returns (output_path, preview_text). No template slot count; no reordering.
    """
    template_file = _ensure_seekable_stream(template_file)
    template_file.seek(0)
    doc = Document(template_file)
    schema = extract_styles(doc)
    style_map = schema.get("style_map") or {}
    style_formatting = schema.get("style_formatting") or {}
    blocks = parse_draft_into_blocks(final_draft_text or "")
    build_document_from_draft(doc, blocks, style_map, style_formatting)
    project_dir = _project_dir()
    output_path = os.path.join(project_dir, "output", "formatted_draft_driven.docx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    preview_text = get_document_preview_text(output_path)
    return output_path, preview_text


def _apply_formatting_overrides(paragraph_sources: list, overrides: dict) -> list:
    """Clone paragraph_sources and apply overrides (e.g. space_after_pt, body_font_size_pt)."""
    if not overrides:
        return paragraph_sources
    out = []
    body_styles = {"Normal", "Body Text", "Paragraphe", "List Paragraph", "List"}
    for src in paragraph_sources:
        s = copy.deepcopy(src)
        pf = s.get("paragraph_format") or {}
        rf = s.get("run_format") or {}
        if "space_after_pt" in overrides and overrides["space_after_pt"] is not None:
            pf["space_after"] = overrides["space_after_pt"]
        if "space_before_pt" in overrides and overrides["space_before_pt"] is not None:
            pf["space_before"] = overrides["space_before_pt"]
        style = (s.get("style") or "").strip()
        if "body_font_size_pt" in overrides and overrides["body_font_size_pt"] is not None:
            if style in body_styles or "normal" in style.lower() or "body" in style.lower():
                rf["size_pt"] = overrides["body_font_size_pt"]
        s["paragraph_format"] = pf
        s["run_format"] = rf
        out.append(s)
    return out


def process_document_from_blocks(blocks: list, template_file, formatting_overrides: dict = None):
    """
    Rebuild a DOCX from pre-segmented blocks and the template (no LLM).
    Use after the user edits the preview text or changes formatting options.
    blocks: list of (block_type, text).
    template_file: file-like or path to template DOCX.
    formatting_overrides: optional dict, e.g. {"space_after_pt": 6, "body_font_size_pt": 11}.
    Returns (output_path, preview_text).
    """
    template_file.seek(0)
    doc = Document(template_file)
    paragraph_sources = extract_paragraph_format_sources(doc)
    if formatting_overrides:
        paragraph_sources = _apply_formatting_overrides(paragraph_sources, formatting_overrides)
    clear_document_body(doc)
    force_single_column(doc)
    inject_blocks_using_paragraph_sources(doc, blocks, paragraph_sources)
    remove_trailing_empty_and_noise(doc)
    project_dir = _project_dir()
    output_path = os.path.join(project_dir, "output", "formatted_edited.docx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    preview_text = get_document_preview_text(output_path)
    return output_path, preview_text


def process_document_structural(
    section_texts: list,
    section_formatting_prompts: list,
    template_file,
) -> tuple:
    """
    7-step structural formatting: parse sample and draft, build rulebook, map draft→template,
    normalize, rebuild signature, validate.
    Returns (output_path, preview_text, all_blocks, structural_result).
    structural_result: {mapping_report, validation, rulebook, template_structure} for UI.
    """
    template_file = _ensure_seekable_stream(template_file)
    template_file.seek(0)
    doc = Document(template_file)
    schema = extract_styles(doc)
    template_file.seek(0)

    # Get draft blocks via existing section-by-section LLM formatting
    all_blocks = []
    n = max(len(section_texts), len(section_formatting_prompts))
    for i in range(n):
        text = section_texts[i] if i < len(section_texts) else ""
        instruction = section_formatting_prompts[i] if i < len(section_formatting_prompts) else ""
        if not text and not instruction:
            continue
        blocks = format_section_with_instruction(text, instruction, schema)
        all_blocks.extend(blocks)

    # Run 7-step structural pipeline (parse sample, rulebook, map, normalize)
    template_file.seek(0)
    sample_doc = Document(template_file)
    result = run_structural_formatting(
        all_blocks,
        sample_doc,
        normalize_text=True,
        validate_after=False,
    )
    mapped_blocks = result["mapped_blocks"]
    paragraph_sources = result["paragraph_sources"]
    template_structure = result["template_structure"]

    # Build output document (Step 6)
    template_file.seek(0)
    output_doc = Document(template_file)
    build_document_from_structural_output(output_doc, mapped_blocks, paragraph_sources)

    # Step 7: Validate
    validation = validate_against_sample(output_doc, template_structure)
    result["validation"] = validation

    project_dir = _project_dir()
    output_path = os.path.join(project_dir, "output", "formatted_structural.docx")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    output_doc.save(output_path)
    preview_text = get_document_preview_text(output_path)

    return output_path, preview_text, mapped_blocks, result
