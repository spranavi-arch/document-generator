"""
Minimal formatter support for the template workflow only.

Used by template_filler: run normalization (black color, no accidental italics)
after merging JSON into template. No geometry cloning, no inject_blocks, no style extraction.
"""

from docx.shared import RGBColor


def force_legal_run_format(paragraph):
    """Force black color on all runs (legal standard). Used after placeholder/block merge."""
    if not paragraph:
        return
    try:
        for run in paragraph.runs:
            try:
                run.font.color.rgb = RGBColor(0, 0, 0)
            except Exception:
                pass
    except Exception:
        pass


def force_legal_run_format_document(doc):
    """Force black color on every run in body paragraphs (legal standard)."""
    if not doc:
        return
    try:
        for paragraph in doc.paragraphs:
            force_legal_run_format(paragraph)
    except Exception:
        pass
