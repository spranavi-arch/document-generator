"""Convert a formatted DOCX (from process_document) into simple, alignment-aware HTML.

This is used to populate the CKEditor content so that the editor preview is a closer
WYSIWYG representation of the downloaded DOCX.
"""
from __future__ import annotations

import html
from typing import Iterable

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from utils.style_extractor import _paragraph_has_bottom_border


def _paragraph_alignment(para) -> str:
    """Return CSS text-align value ('left'|'right'|'center'|'justify') for a paragraph."""
    try:
        align = getattr(para.paragraph_format, "alignment", None)
    except Exception:
        align = None
    if align == WD_ALIGN_PARAGRAPH.CENTER:
        return "center"
    if align == WD_ALIGN_PARAGRAPH.RIGHT:
        return "right"
    if align == WD_ALIGN_PARAGRAPH.JUSTIFY:
        return "justify"
    # Default: treat as left if unset or other
    return "left"


def _escape_text(text: str) -> str:
    """Escape text for HTML and convert embedded newlines to <br>."""
    if text is None:
        return "&nbsp;"
    escaped = html.escape(text, quote=False)
    escaped = escaped.replace("\n", "<br>")
    return escaped if escaped.strip() else "&nbsp;"


def _iter_paragraphs_and_underlines(doc: Document) -> Iterable[str]:
    """Yield HTML snippets (<p> or <hr>) for each paragraph in the DOCX.

    - Paragraphs with only a bottom border (no text) become <hr class="section-underline">
      so html_to_docx can round-trip them via SECTION_UNDERLINE_MARKER if needed.
    - Other paragraphs become <p style="text-align: ...">...</p> with their text.
    """
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        # Section underline: bottom border with no visible text
        if not text and _paragraph_has_bottom_border(para):
            yield '<hr class="section-underline">'
            continue
        align = _paragraph_alignment(para)
        style_attr = f' style="text-align: {align};"' if align != "left" else ""
        yield f"<p{style_attr}>{_escape_text(para.text or '')}</p>"


def docx_to_html(docx_path: str) -> str:
    """Convert a DOCX file into alignment-aware HTML for CKEditor preview.

    The goal is to approximate how the DOCX looks when opened in Word, not to
    perfectly round-trip every style. We preserve:
      - Paragraph alignment (left/center/right/justify)
      - Section underlines (bottom-border-only paragraphs → <hr class=\"section-underline\">)
    """
    if not docx_path:
        return "<p><br></p>"
    try:
        doc = Document(docx_path)
    except Exception:
        return "<p><br></p>"

    parts = list(_iter_paragraphs_and_underlines(doc))
    if not parts:
        return "<p><br></p>"
    return "".join(parts)

