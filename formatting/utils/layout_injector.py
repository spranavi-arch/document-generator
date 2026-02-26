"""
Layout-Aware Mapping: deterministic merge pipeline.

Pipeline:
1. Replace plaintiff name globally (run-safe, entire document).
2. Replace defendant name globally (run-safe, entire document).
3. Caption: index/date/venue by label (keep label, replace value).
4. Allegations: DELETE entire old region, then insert new numbered paragraphs (no partial overlay).
5. Signature block: replace by content heuristics.
6. Footer: replace NOTICE OF ENTRY / SETTLEMENT if provided.

No partial overlays. Caption → global replace. Allegations → delete + rebuild.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from utils.placeholder_docx import (
    paragraph_full_text,
    rebuild_paragraph_single_run,
    replace_globally,
)
from utils.document_structure import extract_structure

try:
    from utils.template_filler import iter_body_blocks
except ImportError:
    from template_filler import iter_body_blocks


def _replace_value_after_label(para, label_pattern: re.Pattern, new_value: str) -> bool:
    """Replace only the value part after a label. Preserves paragraph format."""
    full = paragraph_full_text(para)
    m = label_pattern.match(full)
    if not m:
        return False
    try:
        prefix = m.group(1)
    except IndexError:
        return False
    new_text = prefix + new_value
    rebuild_paragraph_single_run(para, new_text)
    return True


INDEX_NO_LABEL_RE = re.compile(r"^(\s*Index\s+No\.?\s*:?\s*)(.*)$", re.I | re.DOTALL)
DATE_FILED_LABEL_RE = re.compile(r"^(\s*Date\s+Filed\s*:?\s*)(.*)$", re.I | re.DOTALL)


class LayoutAwareInjector:
    """
    Deterministic merge: global name replace, then caption slots, then delete allegation region and rebuild.
    """

    def __init__(self, doc_path: str | Path, structure: dict[str, Any] | None = None):
        self.doc = Document(doc_path)
        if structure is None:
            structure = extract_structure(self.doc)
        self.structure = structure
        self.para_list = self.structure["paragraphs"]

    def _para_at(self, index: int | None):
        if index is None or index < 0 or index >= len(self.para_list):
            return None
        return self.para_list[index][0]

    def inject(self, data: dict[str, Any]) -> None:
        """
        Pipeline: global name replace → caption (index/date/venue) → delete allegation region → insert new allegations → signature → footer.
        """
        cap = self.structure.get("caption") or {}
        new_plaintiff = (data.get("PLAINTIFF_NAME") or data.get("plaintiff") or "").strip()
        new_defendant = (data.get("DEFENDANT_NAME") or data.get("defendant") or "").strip()

        # --- LAYER 1: Replace plaintiff and defendant names GLOBALLY (run-safe, entire doc) ---
        old_plaintiff_para = self._para_at(cap.get("plaintiff_index"))
        old_defendant_para = self._para_at(cap.get("defendant_index"))
        if old_plaintiff_para is not None and new_plaintiff:
            old_plaintiff = paragraph_full_text(old_plaintiff_para).strip()
            if old_plaintiff:
                # Exact match first (caption often has "NAME,")
                replace_globally(self.doc, old_plaintiff, new_plaintiff)
                base = old_plaintiff.rstrip(".,").strip()
                if base and base != old_plaintiff:
                    replace_globally(self.doc, base, new_plaintiff)
                # Case-insensitive so body "Kelley Skaarva" is replaced when caption is "KELLEY SKAARVA"
                replace_globally(self.doc, base or old_plaintiff, new_plaintiff, case_insensitive=True)
        if old_defendant_para is not None and new_defendant:
            old_defendant = paragraph_full_text(old_defendant_para).strip()
            if old_defendant:
                replace_globally(self.doc, old_defendant, new_defendant)
                base = old_defendant.rstrip(".,").strip()
                if base and base != old_defendant:
                    replace_globally(self.doc, base, new_defendant)
                replace_globally(self.doc, base or old_defendant, new_defendant, case_insensitive=True)

        # --- Caption: index, date, venue (label + value) ---
        index_para = self._para_at(cap.get("index_no_index"))
        if index_para is not None:
            val = (data.get("INDEX_NO") or data.get("index_no") or "").strip()
            _replace_value_after_label(index_para, INDEX_NO_LABEL_RE, val)

        date_para = self._para_at(cap.get("date_filed_index"))
        if date_para is not None:
            val = (data.get("DATE_FILED") or data.get("date_filed") or "").strip()
            _replace_value_after_label(date_para, DATE_FILED_LABEL_RE, val)

        venue_para = self._para_at(cap.get("venue_index"))
        if venue_para is not None:
            val = (data.get("PLAINTIFF_RESIDENCE") or data.get("plaintiff_residence") or "").strip()
            if val:
                rebuild_paragraph_single_run(venue_para, val)

        # --- LAYER 2: DELETE entire allegation region, then insert new numbered paragraphs ---
        alg = self.structure.get("allegation_region") or {}
        start_i = alg.get("start")
        end_i = alg.get("end")
        allegations = data.get("CAUSE_OF_ACTION_1_PARAGRAPHS") or data.get("allegations") or []
        if isinstance(allegations, str):
            allegations = [allegations]
        allegations = [str(x) for x in allegations]

        if start_i is not None and end_i is not None:
            first_para = self._para_at(start_i)
            if first_para is not None:
                parent = first_para._element.getparent()
                if parent is not None:
                    # Clone style and pPr BEFORE removing anything (first_para will be removed)
                    try:
                        style_name = first_para.style.name if first_para.style else "Normal"
                    except Exception:
                        style_name = "Normal"
                    src_p = first_para._element
                    pPr = src_p.find(qn("w:pPr"))
                    pPr_clone = copy.deepcopy(pPr) if pPr is not None else None
                    # Strip list numbering so we don't get "32. 31. content" (Word numPr + our text number)
                    if pPr_clone is not None:
                        numPr = pPr_clone.find(qn("w:numPr"))
                        if numPr is not None:
                            pPr_clone.remove(numPr)
                    idx_in_parent = list(parent).index(first_para._element)

                    # Remove ALL paragraphs in [start_i, end_i) from end to start
                    for k in range(end_i - 1, start_i - 1, -1):
                        if k < len(self.para_list):
                            p = self.para_list[k][0]
                            p_parent = p._element.getparent()
                            if p_parent is not None:
                                p_parent.remove(p._element)

                    # Insert new allegation paragraphs at the freed position
                    for i, text in enumerate(allegations):
                        new_p = OxmlElement("w:p")
                        if pPr_clone is not None:
                            new_p.append(copy.deepcopy(pPr_clone))
                        r = OxmlElement("w:r")
                        t_el = OxmlElement("w:t")
                        t_el.text = text
                        r.append(t_el)
                        new_p.append(r)
                        parent.insert(idx_in_parent + i, new_p)
                    for i in range(len(allegations)):
                        new_para = Paragraph(parent[idx_in_parent + i], self.doc)
                        try:
                            new_para.style = style_name
                        except Exception:
                            pass

        # --- Signature block: replace by content heuristics (first match per field to avoid duplication) ---
        sig = self.structure.get("signature_block") or {}
        sig_start = sig.get("start")
        sig_end = sig.get("end")
        replaced_attorney = replaced_firm = replaced_phone = replaced_address = replaced_dated = False
        if sig_start is not None and sig_end is not None:
            for i in range(sig_start, min(sig_end, len(self.para_list))):
                para = self._para_at(i)
                if para is None:
                    continue
                full = paragraph_full_text(para).strip().lower()
                if not replaced_attorney and "esq" in full and (data.get("ATTORNEY_NAME") or data.get("attorney_name")):
                    rebuild_paragraph_single_run(para, (data.get("ATTORNEY_NAME") or data.get("attorney_name") or "").strip())
                    replaced_attorney = True
                elif not replaced_firm and ("pllc" in full or "llc" in full or "p.c." in full) and "law" in full and (data.get("FIRM_NAME") or data.get("firm_name")):
                    rebuild_paragraph_single_run(para, (data.get("FIRM_NAME") or data.get("firm_name") or "").strip())
                    replaced_firm = True
                elif not replaced_phone and re.match(r"^\(?\d{3}\)?\s*\d{3}[-.\s]?\d{4}\s*$", full) and (data.get("PHONE") or data.get("phone")):
                    rebuild_paragraph_single_run(para, (data.get("PHONE") or data.get("phone") or "").strip())
                    replaced_phone = True
                elif not replaced_address and ("avenue" in full or "street" in full or "road" in full or "new york" in full or "connecticut" in full) and (data.get("FIRM_ADDRESS") or data.get("firm_address")):
                    rebuild_paragraph_single_run(para, (data.get("FIRM_ADDRESS") or data.get("firm_address") or "").strip())
                    replaced_address = True
                elif not replaced_dated and "dated" in full and (data.get("SIGNATURE_DATE") or data.get("signature_date")):
                    rebuild_paragraph_single_run(para, (data.get("SIGNATURE_DATE") or data.get("signature_date") or "").strip())
                    replaced_dated = True

        # --- Footer: NOTICE OF ENTRY / NOTICE OF SETTLEMENT (fill body paragraph after each heading) ---
        foot = self.structure.get("footer") or {}
        foot_start = foot.get("start")
        foot_end = foot.get("end")
        notice_entry_val = (data.get("NOTICE_OF_ENTRY") or data.get("notice_of_entry") or "").strip()
        notice_settlement_val = (data.get("NOTICE_OF_SETTLEMENT") or data.get("notice_of_settlement") or "").strip()
        filled_entry = filled_settlement = False
        if foot_start is not None and foot_end is not None:
            for i in range(foot_start, min(foot_end, len(self.para_list))):
                para = self._para_at(i)
                if para is None:
                    continue
                full_upper = paragraph_full_text(para).upper()
                # If this para is the NOTICE OF ENTRY heading (and optionally body), fill it or the next para
                if not filled_entry and "NOTICE OF ENTRY" in full_upper and notice_entry_val:
                    # Same paragraph contains body template ("that the within", "duly entered") → replace whole para
                    if "THAT THE WITHIN" in full_upper or "DULY ENTERED" in full_upper:
                        rebuild_paragraph_single_run(para, notice_entry_val)
                    else:
                        next_para = self._para_at(i + 1)
                        if next_para is not None:
                            rebuild_paragraph_single_run(next_para, notice_entry_val)
                    filled_entry = True
                elif not filled_settlement and "NOTICE OF SETTLEMENT" in full_upper and notice_settlement_val:
                    if "PRESENTED FOR SETTLEMENT" in full_upper or "ORDER OF WHICH" in full_upper:
                        rebuild_paragraph_single_run(para, notice_settlement_val)
                    else:
                        next_para = self._para_at(i + 1)
                        if next_para is not None:
                            rebuild_paragraph_single_run(next_para, notice_settlement_val)
                    filled_settlement = True

    def save(self, output_path: str | Path) -> None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(output_path)


def inject_content_into_layout(
    doc_path: str | Path,
    data: dict[str, Any],
    output_path: str | Path,
    structure: dict[str, Any] | None = None,
) -> None:
    """
    Deterministic pipeline: global name replace → caption → delete allegation region → insert new allegations → signature → footer → save.
    """
    injector = LayoutAwareInjector(doc_path, structure=structure)
    injector.inject(data)
    injector.save(output_path)
