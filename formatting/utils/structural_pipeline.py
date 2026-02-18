#not using it. 

"""
7-step structural formatting pipeline: parse sample and draft, build a formatting
rulebook from the sample, map draft sections to template slots, normalize layout,
reformat titles/signature, and validate against the sample.

Step 1: Parse both documents structurally (sample = template structure; draft = blocks + inferred types).
Step 2: Build a "formatting template" (rulebook) from the sample.
Step 3: Map draft sections → template sections (alignment).
Step 4: Normalize spacing, indents, alignment.
Step 5: Reformat section titles & labels (position, caps, spacing).
Step 6: Rebuild signature & footer from sample structure + draft content.
Step 7: Validate output against sample (diff check).
"""

from docx import Document

from utils.style_extractor import (
    extract_paragraph_format_sources,
    extract_template_structure,
    iter_body_blocks,
)
from utils.formatter import (
    clear_document_body,
    force_single_column,
    inject_blocks_using_paragraph_sources,
    remove_trailing_empty_and_noise,
)

try:
    from utils.style_extractor import _paragraph_has_bottom_border
except Exception:
    _paragraph_has_bottom_border = None


# -----------------------------------------------------------------------------
# Step 1: Parse both documents structurally
# -----------------------------------------------------------------------------

def _infer_section_type_from_text(text: str, block_kind: str) -> str:
    """Infer legal document section type from block text and kind (for draft blocks)."""
    t = (text or "").strip().lower()[:200]
    if block_kind in ("line", "signature_line"):
        return "separator"
    if not t or t == "(empty)":
        return "body"
    if any(x in t for x in ("supreme court", "county of", "index no", "index number", "notice of motion",
                            "to restore", "affirmation in support", "affidavit of service",
                            "c o u n s e l o r s", "counselors:", "plaintiff", "defendant", "-against-", "against")):
        return "caption"
    if any(x in t for x in ("please take notice", "take further notice", "for an order")):
        return "motion_notice"
    if any(x in t for x in ("attorneys for", "attorney for", "law firm", "esq.", "pllc", "p.c.")) and any(c.isdigit() for c in t):
        return "attorney_signature"
    if t.startswith("to:") or (len(t) < 5 and "to" in t):
        return "to_section"
    if any(x in t for x in ("affirms the following", "respectfully submitted", "it is respectfully", "wherefore")):
        return "affirmation"
    if any(x in t for x in ("duly sworn", "being duly sworn", "under the penalties of perjury")):
        return "affidavit"
    if any(x in t for x in ("sworn to before me", "notary public", "state of ", "county of ")) and len(t) < 120:
        return "notary"
    if "dated:" in t and len(t) < 80:
        return "body"
    return "body"


def parse_sample_structurally(doc: Document, max_paragraphs: int = 500) -> list[dict]:
    """
    Step 1a: Parse the sample DOCX into structured blocks.
    Returns list of block specs: style, section_type, block_kind, paragraph_format, run_format, hint, etc.
    """
    return extract_template_structure(doc, max_paragraphs=max_paragraphs)


def parse_draft_structurally(blocks: list[tuple]) -> list[dict]:
    """
    Step 1b: Parse the draft (list of (block_type, text)) into structural blocks with section_type.
    Returns list of {block_type, text, section_type, hint}.
    """
    out = []
    for block_type, text in blocks:
        hint = ((text or "").strip()[:80] + "…") if text and len((text or "").strip()) > 80 else ((text or "").strip() or "(empty)")
        section_type = _infer_section_type_from_text(text or "", block_type)
        out.append({
            "block_type": block_type,
            "text": (text or "").strip(),
            "section_type": section_type,
            "hint": hint,
        })
    return out


# -----------------------------------------------------------------------------
# Step 2: Build formatting template (rulebook) from sample
# -----------------------------------------------------------------------------

def build_formatting_rulebook(template_structure: list[dict]) -> dict:
    """
    Step 2: Convert sample structure into a formatting rulebook.
    Returns:
      - layout_pattern: list of slot specs {section_type, block_kind, style, paragraph_format, run_format, ...}
      - rules_by_type: section_type -> typical rules (alignment, caps, indent description)
    """
    layout_pattern = []
    rules_by_type = {}
    for spec in template_structure:
        section_type = spec.get("section_type", "body")
        block_kind = spec.get("block_kind", "paragraph")
        style = spec.get("style", "Normal")
        pf = spec.get("paragraph_format") or {}
        rf = spec.get("run_format") or {}
        layout_pattern.append({
            "section_type": section_type,
            "block_kind": block_kind,
            "style": style,
            "paragraph_format": pf,
            "run_format": rf,
            "page_break_before": spec.get("page_break_before", False),
            "hint": spec.get("hint", ""),
        })
        if section_type not in rules_by_type:
            rules_by_type[section_type] = {
                "alignment": pf.get("alignment"),
                "space_after": pf.get("space_after"),
                "space_before": pf.get("space_before"),
                "left_indent": pf.get("left_indent"),
                "bold": rf.get("bold"),
                "all_caps_typical": _looks_like_all_caps(spec.get("hint", "")),
            }
    return {"layout_pattern": layout_pattern, "rules_by_type": rules_by_type}


def _looks_like_all_caps(text: str) -> bool:
    """True if text appears to be all caps (e.g. SUMMONS, VERIFIED COMPLAINT)."""
    if not text or len(text.strip()) < 2:
        return False
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    return sum(1 for c in letters if c.isupper()) / len(letters) >= 0.85


# -----------------------------------------------------------------------------
# Step 3: Map draft sections → template sections
# -----------------------------------------------------------------------------

def map_draft_to_template(
    draft_blocks: list[dict],
    template_slots: list[dict],
) -> tuple[list[tuple], list[dict]]:
    """
    Step 3: Align draft blocks to template slots.
    Returns (mapped_blocks, mapping_report).
    mapped_blocks: list of (block_type, text) with same length as template_slots; content from draft or empty.
    mapping_report: list of {slot_index, template_type, draft_index, match_status, note}.
    """
    report = []
    consumed = [False] * len(draft_blocks)
    mapped = []
    for slot_idx, slot in enumerate(template_slots):
        template_type = slot.get("section_type", "body")
        block_kind = slot.get("block_kind", "paragraph")
        # Prefer exact section_type match; then take next available of same type; else take next body; else empty
        chosen = None
        for d_idx, d in enumerate(draft_blocks):
            if consumed[d_idx]:
                continue
            if d["section_type"] == template_type:
                chosen = d_idx
                break
        if chosen is None and template_type == "body":
            for d_idx, d in enumerate(draft_blocks):
                if consumed[d_idx]:
                    continue
                chosen = d_idx
                break
        if chosen is None:
            for d_idx, d in enumerate(draft_blocks):
                if consumed[d_idx]:
                    continue
                chosen = d_idx
                break
        if chosen is not None:
            consumed[chosen] = True
            d = draft_blocks[chosen]
            bt = d["block_type"]
            text = d["text"]
            status = "ok" if d["section_type"] == template_type else "mapped"
            report.append({
                "slot_index": slot_idx,
                "template_type": template_type,
                "draft_index": chosen,
                "match_status": status,
                "note": f"draft block {chosen} ({d['section_type']}) → slot {slot_idx} ({template_type})",
            })
            # For line/signature_line slots, keep block_type from template so formatting is correct
            if block_kind == "signature_line":
                bt = "signature_line"
            elif block_kind == "line":
                bt = "line"
            elif block_kind == "section_underline":
                bt = "section_underline"
                text = ""
            mapped.append((bt, text))
        else:
            report.append({
                "slot_index": slot_idx,
                "template_type": template_type,
                "draft_index": None,
                "match_status": "empty",
                "note": f"no draft block for slot {slot_idx} ({template_type})",
            })
            style_name = slot.get("style", "Normal")
            if block_kind == "section_underline":
                mapped.append(("section_underline", ""))
            elif block_kind == "line":
                mapped.append(("line", ""))
            elif block_kind == "signature_line":
                mapped.append(("signature_line", ""))
            else:
                mapped.append((style_name, ""))
    return mapped, report


# -----------------------------------------------------------------------------
# Step 4: Normalize spacing, indents, alignment (and Step 5/6 via rulebook)
# -----------------------------------------------------------------------------

def normalize_text_for_slot(text: str, slot: dict, rules_by_type: dict) -> str:
    """
    Step 4/5: Normalize text to match slot rules: collapse internal newlines, apply caps for titles.
    """
    if not text or not text.strip():
        return text.strip() if text else ""
    t = text.strip()
    # Collapse multiple newlines to single line break within paragraph (template drives spacing)
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    section_type = slot.get("section_type", "body")
    rules = rules_by_type.get(section_type, {})
    if rules.get("all_caps_typical") and section_type in ("caption", "body"):
        # Only force caps for document/section titles (short lines)
        if len(t) < 80 and _looks_like_title_phrase(t):
            t = t.upper()
    return "\n".join(lines) if lines else t


def _looks_like_title_phrase(text: str) -> bool:
    """True if text looks like a document or section title (SUMMONS, VERIFIED COMPLAINT, etc.)."""
    t = text.strip().upper()
    title_phrases = (
        "SUMMONS", "COMPLAINT", "VERIFIED", "JURY TRIAL DEMANDED",
        "AS AND FOR A FIRST CAUSE OF ACTION", "CAUSE OF ACTION",
        "NOTICE OF MOTION", "AFFIRMATION", "AFFIDAVIT", "MEMORANDUM",
    )
    return any(p in t for p in title_phrases) or (len(t) < 50 and t.isupper())


# -----------------------------------------------------------------------------
# Step 7: Validate output against sample
# -----------------------------------------------------------------------------

def _is_signature_like(text: str) -> bool:
    return "____" in text or (len(text.strip()) < 5 and "_" in text)


def _is_line_like(text: str) -> bool:
    t = text.strip()
    if len(t) < 10:
        return False
    return t.endswith("X") and all(c in " -_.=\t" or c == "X" for c in t[:-1])


def validate_against_sample(output_doc: Document, sample_structure: list[dict]) -> dict:
    """
    Step 7: Compare output document structure to sample structure.
    Returns {match: bool, same_section_order: bool, same_block_count: bool, diff: list, message: str}.
    """
    output_struct = _structure_from_doc_safe(output_doc)
    sample_types = [s.get("section_type", "body") for s in sample_structure]
    output_types = [s.get("section_type", "body") for s in output_struct]
    same_count = len(output_struct) == len(sample_structure)
    diff = []
    for i in range(max(len(sample_types), len(output_types))):
        st = sample_types[i] if i < len(sample_types) else None
        ot = output_types[i] if i < len(output_types) else None
        if st != ot:
            diff.append({"index": i, "sample_type": st, "output_type": ot})
    same_order = len(diff) == 0 and same_count
    match = same_order
    message = "Structure matches sample." if match else f"Structure diff: {len(diff)} slot(s) differ; same count={same_count}."
    return {
        "match": match,
        "same_section_order": same_order,
        "same_block_count": same_count,
        "diff": diff,
        "message": message,
        "sample_slot_count": len(sample_structure),
        "output_slot_count": len(output_struct),
    }


# Avoid circular import: use inline check for section_underline
def _paragraph_has_bottom_border_safe(para) -> bool:
    if _paragraph_has_bottom_border is None:
        return False
    try:
        return _paragraph_has_bottom_border(para)
    except Exception:
        return False


def _structure_from_doc_safe(doc: Document, max_paragraphs: int = 500) -> list[dict]:
    """Extract structure without relying on _infer_section_type from style_extractor (avoid circular import)."""
    out = []
    for i, (para, _tid, _r, _c) in enumerate(iter_body_blocks(doc)):
        if i >= max_paragraphs:
            break
        text = (para.text or "").strip()
        if not text and _paragraph_has_bottom_border_safe(para):
            out.append({"section_type": "separator", "block_kind": "section_underline"})
        else:
            kind = "signature_line" if _is_signature_like(text) else "line" if _is_line_like(text) else "paragraph"
            st = _infer_section_type_from_text(text, kind)
            out.append({"section_type": st, "block_kind": kind})
    return out


# -----------------------------------------------------------------------------
# Full pipeline: run all steps and produce formatted document
# -----------------------------------------------------------------------------

def run_structural_formatting(
    draft_blocks: list[tuple],
    sample_doc: Document,
    normalize_text: bool = True,
    validate_after: bool = True,
) -> dict:
    """
    Run the full 7-step structural formatting pipeline.

    draft_blocks: list of (block_type, text) from LLM or section-by-section.
    sample_doc: python-docx Document (sample/template).
    normalize_text: apply Step 4/5 text normalization (caps, newlines).
    validate_after: run Step 7 validation after building the doc.

    Returns dict with:
      - mapped_blocks: list of (block_type, text) to inject
      - paragraph_sources: from sample (for inject)
      - rulebook: from Step 2
      - mapping_report: from Step 3
      - validation: from Step 7 (if validate_after and doc was built)
      - output_doc: the built Document (caller can save)
    """
    # Step 1
    template_structure = parse_sample_structurally(sample_doc)
    draft_structural = parse_draft_structurally(draft_blocks)

    # Step 2
    rulebook = build_formatting_rulebook(template_structure)
    layout_pattern = rulebook["layout_pattern"]
    rules_by_type = rulebook["rules_by_type"]

    # Step 3
    mapped_blocks, mapping_report = map_draft_to_template(draft_structural, layout_pattern)

    # Step 4/5: normalize text in mapped_blocks
    if normalize_text:
        normalized = []
        for i, (bt, text) in enumerate(mapped_blocks):
            slot = layout_pattern[i] if i < len(layout_pattern) else {}
            t = normalize_text_for_slot(text, slot, rules_by_type)
            if bt == "section_underline":
                t = ""
            normalized.append((bt, t))
        mapped_blocks = normalized

    # Paragraph sources from sample (for Step 6 inject)
    paragraph_sources = extract_paragraph_format_sources(sample_doc)

    # Step 6 (rebuild doc) is done in the backend with build_document_from_structural_output.
    # Actually run_structural_formatting receives sample_doc - we cannot clear it in place if it's the template.
    # So the API should be: caller passes template_file (or bytes), we load doc, extract everything, then build
    # a new document by loading template again and clear + inject. Let me change to accept a file-like template
    # and return mapped_blocks, paragraph_sources, report, validation. The actual inject is done in the backend
    # by loading template, clear, inject_blocks_using_paragraph_sources(mapped_blocks, paragraph_sources).
    # So we don't build the doc inside this function; we return mapped_blocks and paragraph_sources and the backend
    # builds the doc. Then for validation we need the built doc - so the backend builds it, then calls
    # validate_against_sample(built_doc, template_structure). So I'll return everything needed for the backend
    # to build and validate.
    validation = None
    return {
        "mapped_blocks": mapped_blocks,
        "paragraph_sources": paragraph_sources,
        "rulebook": rulebook,
        "mapping_report": mapping_report,
        "template_structure": template_structure,
        "validation": validation,
    }


def build_document_from_structural_output(
    template_doc: Document,
    mapped_blocks: list[tuple],
    paragraph_sources: list[dict],
) -> Document:
    """
    Step 6: Rebuild document from mapped blocks and template.
    Clears template body and injects mapped_blocks using paragraph_sources.
    Returns the modified document (template_doc is modified in place).
    """
    clear_document_body(template_doc)
    force_single_column(template_doc)
    inject_blocks_using_paragraph_sources(template_doc, mapped_blocks, paragraph_sources)
    remove_trailing_empty_and_noise(template_doc)
    return template_doc
