"""
Layout-Aware Mapping: extract document structure from a sample DOCX.

Detects regions by markers (no hardcoded placeholders):
- Caption block: "-against-" markers
- Allegation region: first numbered paragraph after "AS AND FOR A FIRST CAUSE OF ACTION"
- Signature block: first line containing "ESQ"
- Footer: "NOTICE OF ENTRY" or "NOTICE OF SETTLEMENT"

Returns structure with paragraph indices and slot roles for LayoutAwareInjector.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document

from utils.placeholder_docx import paragraph_full_text

try:
    from utils.template_filler import iter_body_blocks
except ImportError:
    from template_filler import iter_body_blocks


def _para_list(doc: Document) -> list[tuple[Any, int | None, int | None, int | None]]:
    """List of (paragraph, table_id, row, col) in document order."""
    return list(iter_body_blocks(doc))


def _text(i: int, para_list: list, get_text=paragraph_full_text) -> str:
    """Full text of paragraph at index i."""
    if i < 0 or i >= len(para_list):
        return ""
    return (get_text(para_list[i][0]) or "").strip()


def extract_structure(doc: Document) -> dict[str, Any]:
    """
    Detect caption, allegation region, signature block, footer from document structure.
    Returns structure dict with 0-based paragraph indices (document order = body + table cells).
    """
    para_list = _para_list(doc)
    n = len(para_list)
    structure: dict[str, Any] = {
        "paragraphs": para_list,
        "caption": {"start": 0, "end": 0, "plaintiff_index": None, "defendant_index": None, "index_no_index": None, "date_filed_index": None, "venue_index": None},
        "allegation_region": {"start": None, "end": None},
        "signature_block": {"start": None, "end": None},
        "footer": {"start": None, "end": None},
    }

    # --- Caption: up to and including the block containing "-against-" and defendant line ---
    against_i = None
    for i in range(n):
        t = _text(i, para_list).lower()
        if "-against-" in t or "against" in t and re.search(r"\bagainst\b", t, re.I):
            against_i = i
            break
    if against_i is not None:
        # Caption start: N paragraphs before "-against-" to include court/plaintiff (caption at top of doc)
        structure["caption"]["start"] = max(0, against_i - 15)
        # Caption end: a few lines after "-against-" to include defendant line (e.g. next 2–3 paras)
        caption_end = min(against_i + 4, n)
        for j in range(against_i + 1, min(against_i + 5, n)):
            tt = _text(j, para_list).lower()
            if "defendant" in tt or "plaintiff" in tt:
                caption_end = j + 1
                break
        # Extend caption to include Index No. / Date Filed / venue when they follow defendant
        for j in range(caption_end, min(caption_end + 6, n)):
            tt = _text(j, para_list)
            if re.match(r"^\s*Index\s+No\.?\s*:?\s*", tt, re.I) or re.match(r"^\s*Date\s+Filed\s*:?\s*", tt, re.I) or "venue" in tt.lower() or "residence" in tt.lower():
                caption_end = j + 1
            else:
                break
        structure["caption"]["end"] = caption_end
        # Plaintiff/defendant lines: ALL CAPS line immediately before "Plaintiff," / "Defendant."
        for i in range(caption_end):
            t = _text(i, para_list)
            lower = t.lower()
            next_t = _text(i + 1, para_list).lower()
            if next_t in ("plaintiff,", "plaintiff", "claimant,", "claimant") and t.isupper() and len(t) <= 80:
                if structure["caption"]["plaintiff_index"] is None:
                    structure["caption"]["plaintiff_index"] = i
            if next_t in ("defendant.", "defendant", "respondent.", "respondent") and t.isupper() and len(t) <= 80:
                if structure["caption"]["defendant_index"] is None:
                    structure["caption"]["defendant_index"] = i
            if re.match(r"^\s*Index\s+No\.?\s*:?\s*", t, re.I):
                if structure["caption"]["index_no_index"] is None:
                    structure["caption"]["index_no_index"] = i
            if re.match(r"^\s*Date\s+Filed\s*:?\s*", t, re.I):
                if structure["caption"]["date_filed_index"] is None:
                    structure["caption"]["date_filed_index"] = i
            if "basis of venue" in lower or "venue is" in lower or "plaintiff's residence" in lower:
                if structure["caption"]["venue_index"] is None:
                    structure["caption"]["venue_index"] = i

    # --- Allegation region: after "AS AND FOR A FIRST CAUSE OF ACTION", first numbered para ---
    cause_action_i = None
    for i in range(n):
        t = _text(i, para_list)
        if "as and for" in t.lower() and "cause of action" in t.lower():
            cause_action_i = i
            break
    if cause_action_i is not None:
        allegation_start = None
        for j in range(cause_action_i + 1, n):
            t = _text(j, para_list)
            if not t:
                continue
            if re.match(r"^\s*\d+\.\s+", t) or re.match(r"^\s*That\s+", t, re.I) or re.match(r"^\s*By\s+reason\s+of", t, re.I):
                allegation_start = j
                break
        if allegation_start is not None:
            allegation_end = allegation_start
            for j in range(allegation_start, n):
                t = _text(j, para_list).lower()
                if "wherefore" in t or ("cause of action" in t and j > allegation_start) or "esq" in t:
                    allegation_end = j
                    break
                allegation_end = j + 1
            structure["allegation_region"]["start"] = allegation_start
            structure["allegation_region"]["end"] = allegation_end

    # --- Signature block: first para containing "ESQ" ---
    for i in range(n):
        if "esq" in _text(i, para_list).lower():
            structure["signature_block"]["start"] = i
            # End: before footer or next section (e.g. NOTICE OF ENTRY) or ~10 paras
            end = min(i + 12, n)
            for j in range(i + 1, end):
                t = _text(j, para_list).upper()
                if "NOTICE OF ENTRY" in t or "NOTICE OF SETTLEMENT" in t:
                    end = j
                    break
            structure["signature_block"]["end"] = end
            break

    # --- Footer: NOTICE OF ENTRY or NOTICE OF SETTLEMENT ---
    for i in range(n):
        t = _text(i, para_list).upper()
        if "NOTICE OF ENTRY" in t or "NOTICE OF SETTLEMENT" in t:
            structure["footer"]["start"] = i
            structure["footer"]["end"] = n
            break

    return structure


class DocumentStructureExtractor:
    """Extract layout structure from a DOCX (caption, allegation region, signature, footer)."""

    def __init__(self, doc: Document):
        self.doc = doc
        self._structure: dict[str, Any] | None = None

    def extract(self) -> dict[str, Any]:
        """Detect and return structure. Cached after first call."""
        if self._structure is None:
            self._structure = extract_structure(self.doc)
        return self._structure

    @classmethod
    def from_path(cls, path: str | Path) -> "DocumentStructureExtractor":
        return cls(Document(path))
