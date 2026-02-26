"""
Phase 4: Merge JSON into template.

Run-safe: replace at PARAGRAPH level. Full paragraph text = concat all runs;
apply all placeholder replacements (all occurrences); rebuild paragraph into
a SINGLE run. Do not change paragraph style/alignment/tabstops/borders.

After filling: validate no unreplaced {{...}} remain; fail if PLAINTIFF_NAME or
DEFENDANT_NAME replacement count is 0.
"""

from __future__ import annotations

import copy
import logging
import re
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph
from docx.table import Table

from utils.placeholder_docx import paragraph_full_text, rebuild_paragraph_single_run

log = logging.getLogger(__name__)

# Pattern for unreplaced placeholders after fill
REMAINING_PLACEHOLDER_PATTERN = re.compile(r"\{\{([^}]+)\}\}")


def _iter_body_blocks(doc: Document):
    """Yield (paragraph, table_id, row, col) in document order."""
    body = doc.element.body
    qp, qt = qn("w:p"), qn("w:tbl")
    table_id = 0
    for child in body.iterchildren():
        if child.tag == qp:
            yield Paragraph(child, doc), None, None, None
        elif child.tag == qt:
            tbl = Table(child, doc)
            for ri, row in enumerate(tbl.rows):
                for ci, cell in enumerate(row.cells):
                    for para in cell.paragraphs:
                        yield para, table_id, ri, ci
            table_id += 1


def iter_body_blocks(doc: Document):
    """Public alias for template iteration (used by template_debug)."""
    return _iter_body_blocks(doc)


def _apply_replacements_to_paragraph(
    para: Paragraph,
    replacements: dict[str, str],
    counts: dict[str, int],
) -> None:
    """
    Get full paragraph text (run-safe), apply all replacements (all occurrences each),
    rebuild single run. replacements: marker -> value (e.g. "{{PLAINTIFF_NAME}}" -> "JOHN DOE").
    """
    full = paragraph_full_text(para)
    if not full:
        return
    changed = False
    for marker, value in replacements.items():
        if not marker or marker not in full:
            continue
        n = full.count(marker)
        if n > 0:
            key = marker[2:-2] if marker.startswith("{{") and marker.endswith("}}") else marker
            counts[key] = counts.get(key, 0) + n
            full = full.replace(marker, value)
            changed = True
    if changed:
        rebuild_paragraph_single_run(para, full)


def _find_remaining_placeholders(doc: Document) -> set[str]:
    """Scan all paragraphs and table cells for any {{...}} tokens. Return set of keys (e.g. PLAINTIFF_NAME)."""
    remaining = set()
    for para, _tid, _r, _c in _iter_body_blocks(doc):
        full = paragraph_full_text(para)
        for m in REMAINING_PLACEHOLDER_PATTERN.finditer(full):
            key = m.group(1).strip()
            if key:
                remaining.add(key)
    return remaining


class TemplateFiller:
    """
    Fill a template DOCX with scalar values and optional block (numbered paragraphs).
    Paragraph-level, run-safe: only replace text; single run per paragraph after replace.
    """

    def __init__(self, template_path: str | Path):
        self.doc = Document(template_path)
        self._template_path = Path(template_path)
        self._counts: dict[str, int] = {}

    def fill_scalar(self, key: str, value: str) -> dict[str, int]:
        """
        Replace {{KEY}} with value in all paragraphs and table cells (all occurrences).
        Returns counts per key for this key only (for logging).
        """
        if value is None:
            value = ""
        value = str(value)
        marker = "{{" + key + "}}"
        replacements = {marker: value}
        counts: dict[str, int] = {}
        for para, _tid, _r, _c in _iter_body_blocks(self.doc):
            _apply_replacements_to_paragraph(para, replacements, counts)
        self._counts[key] = self._counts.get(key, 0) + counts.get(key, 0)
        return counts

    def fill_all_scalars(self, replacements: dict[str, str]) -> dict[str, int]:
        """
        Apply all scalar replacements in one pass per paragraph (run-safe, all occurrences).
        replacements: key -> value (e.g. PLAINTIFF_NAME -> "JOHN DOE"); keys are without braces.
        Returns total replacement counts per key.
        """
        marker_to_value = {"{{" + k + "}}": str(v) if v is not None else "" for k, v in replacements.items()}
        counts: dict[str, int] = {}
        for para, _tid, _r, _c in _iter_body_blocks(self.doc):
            _apply_replacements_to_paragraph(para, marker_to_value, counts)
        self._counts = counts
        log.info("Fill replacement counts: %s", counts)
        for key, n in sorted(counts.items()):
            log.info("  %s: %d", key, n)
        return counts

    def fill_block(self, key: str, list_of_paragraphs: list[str]) -> None:
        """
        Locate paragraph containing {{KEY__BLOCK}}, store its paragraph style,
        delete that paragraph, insert new numbered paragraphs with stored style.
        """
        block_marker = "{{" + key + "__BLOCK}}"
        source_para = None
        for para, table_id, row, col in _iter_body_blocks(self.doc):
            if block_marker in paragraph_full_text(para):
                source_para = para
                break
        if source_para is None:
            return
        try:
            style_name = source_para.style.name if source_para.style else "Normal"
        except Exception:
            style_name = "Normal"
        src_pPr = source_para._element.find(qn("w:pPr"))
        parent = source_para._element.getparent()
        if parent is None:
            return
        idx = list(parent).index(source_para._element)
        parent.remove(source_para._element)
        for i, text in enumerate(list_of_paragraphs or []):
            new_p = OxmlElement("w:p")
            if src_pPr is not None:
                new_p.append(copy.deepcopy(src_pPr))
            r = OxmlElement("w:r")
            t = OxmlElement("w:t")
            t.text = str(text)
            r.append(t)
            new_p.append(r)
            parent.insert(idx + i, new_p)
        for i in range(len(list_of_paragraphs or [])):
            new_para = Paragraph(parent[idx + i], self.doc)
            try:
                new_para.style = style_name
            except Exception:
                pass

    def validate_no_placeholders_remaining(self) -> None:
        """Raise ValueError if any {{...}} tokens remain in the document (lists missing keys)."""
        remaining = _find_remaining_placeholders(self.doc)
        if remaining:
            raise ValueError(
                "After fill, unreplaced placeholders remain: " + ", ".join(sorted(remaining))
            )

    def save(self, output_path: str | Path) -> None:
        """Save filled document to path."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(output_path)


def fill_template_from_data(
    template_path: str | Path,
    data: dict,
    output_path: str | Path,
    schema_spec: dict | None = None,
    *,
    validate: bool = True,
    require_party_names: bool = True,
) -> dict[str, int]:
    """
    Load template, fill scalars (paragraph-level, all occurrences) then block, validate, save.
    If validate=True, raises if any {{...}} remain. If require_party_names=True, raises if
    PLAINTIFF_NAME or DEFENDANT_NAME replacement count is 0.
    Returns replacement counts per key.
    """
    from utils.schema import SUMMONS_SCHEMA_SPEC
    spec = schema_spec or SUMMONS_SCHEMA_SPEC
    filler = TemplateFiller(template_path)
    # Which placeholders exist in the template (so we only require count > 0 for those)
    placeholders_in_template = _find_remaining_placeholders(filler.doc)
    # Build scalar replacements
    replacements: dict[str, str] = {}
    for key in spec:
        if key == "CAUSE_OF_ACTION_1_PARAGRAPHS":
            continue
        val = data.get(key)
        replacements[key] = str(val) if val is not None else ""
    counts = filler.fill_all_scalars(replacements)
    if require_party_names:
        for key in ("PLAINTIFF_NAME", "DEFENDANT_NAME"):
            if key in placeholders_in_template and counts.get(key, 0) == 0:
                raise ValueError(
                    f"{key} replacement count is 0 but template contains {{{{ {key} }}}}; "
                    "provide a non-empty value in fill data or rebuild template from sample."
                )
    filler.fill_block("CAUSE_OF_ACTION_1_PARAGRAPHS", data.get("CAUSE_OF_ACTION_1_PARAGRAPHS") or [])
    if validate:
        filler.validate_no_placeholders_remaining()
    filler.save(output_path)
    return counts
