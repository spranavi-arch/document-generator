"""
Deterministic style blueprint from DOCX structure (python-docx only).

Extract paragraph styles, paragraph_format, tab_stops, borders, run styles
into a JSON blueprint. No images, no pixels — reproducible from the document.
Use for documentation or automation; main flow still freezes geometry by
using the DOCX as template and only replacing text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.enum.text import WD_ALIGN_PARAGRAPH

from utils.template_filler import iter_body_blocks


def _alignment_to_str(alignment) -> str | None:
    if alignment is None:
        return None
    if alignment == WD_ALIGN_PARAGRAPH.LEFT:
        return "LEFT"
    if alignment == WD_ALIGN_PARAGRAPH.CENTER:
        return "CENTER"
    if alignment == WD_ALIGN_PARAGRAPH.RIGHT:
        return "RIGHT"
    if alignment == WD_ALIGN_PARAGRAPH.JUSTIFY:
        return "JUSTIFY"
    return str(alignment)


def _length_pt(obj) -> float | None:
    if obj is None:
        return None
    try:
        return getattr(obj, "pt", None)
    except Exception:
        return None


def _has_bottom_border(para) -> bool:
    try:
        p = getattr(para, "_element", para._p if hasattr(para, "_p") else None)
        if p is None:
            return False
        pPr = p.find(qn("w:pPr"))
        if pPr is None:
            return False
        pBdr = pPr.find(qn("w:pBdr"))
        if pBdr is None:
            return False
        return pBdr.find(qn("w:bottom")) is not None
    except Exception:
        return False


def _tab_stops_list(para) -> list[dict[str, Any]]:
    out = []
    try:
        tab_stops = para.paragraph_format.tab_stops
        if tab_stops is None:
            return out
        for ts in tab_stops:
            pos = _length_pt(ts.position) if hasattr(ts, "position") else None
            align = None
            if hasattr(ts, "alignment"):
                a = ts.alignment
                if a is not None:
                    align = str(a).split(".")[-1] if "." in str(a) else str(a)
            out.append({"position_pt": pos, "alignment": align})
    except Exception:
        pass
    return out


def _run_format_summary(para) -> dict[str, Any]:
    """First run's font/size/bold/italic as representative run format."""
    if not para.runs:
        return {}
    r = para.runs[0]
    out = {}
    try:
        if r.bold is not None:
            out["bold"] = r.bold
    except Exception:
        pass
    try:
        if r.italic is not None:
            out["italic"] = r.italic
    except Exception:
        pass
    try:
        if r.font.size is not None:
            out["font_size_pt"] = _length_pt(r.font.size)
    except Exception:
        pass
    return out


def extract_paragraph_geometry(para) -> dict[str, Any]:
    """Extract one paragraph's geometry and run hints (deterministic, from DOCX only)."""
    pf = para.paragraph_format
    geom = {
        "alignment": _alignment_to_str(para.alignment),
        "border_bottom": _has_bottom_border(para),
        "tab_stops": _tab_stops_list(para),
        "first_line_indent_pt": _length_pt(pf.first_line_indent),
        "left_indent_pt": _length_pt(pf.left_indent),
        "right_indent_pt": _length_pt(pf.right_indent),
        "space_before_pt": _length_pt(pf.space_before),
        "space_after_pt": _length_pt(pf.space_after),
    }
    run_fmt = _run_format_summary(para)
    if run_fmt:
        geom["run"] = run_fmt
    return geom


def build_style_blueprint(doc: Document) -> dict[str, Any]:
    """
    Build a style blueprint JSON from the document.

    Groups paragraphs by style name and records one representative geometry per style
    (alignment, border_bottom, tab_stops, first_line_indent, etc.). Deterministic and
    reproducible from the DOCX structure — no images or pixels.

    Returns:
        {
          "caption_block": { "alignment": "CENTER", "border_bottom": true, "tab_stops": [...], "first_line_indent_pt": 0, ... },
          "Normal": { ... },
          ...
        }
    """
    by_style: dict[str, dict[str, Any]] = {}
    for para, _tid, _r, _c in iter_body_blocks(doc):
        try:
            style_name = para.style.name if para.style else "Normal"
        except Exception:
            style_name = "Normal"
        if style_name not in by_style:
            by_style[style_name] = extract_paragraph_geometry(para)
    return by_style


def build_style_blueprint_from_path(docx_path: str | Path) -> dict[str, Any]:
    """Load DOCX and return style blueprint."""
    doc = Document(docx_path)
    return build_style_blueprint(doc)


def save_style_blueprint(docx_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    """
    Extract style blueprint from DOCX and optionally save as JSON.
    Returns the blueprint dict.
    """
    blueprint = build_style_blueprint_from_path(docx_path)
    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(blueprint, f, indent=2)
    return blueprint
