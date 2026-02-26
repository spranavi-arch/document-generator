"""
Structural diff for two-sample template mining.

Normalize both documents to segment lists, then compare section-by-section.
Differing spans are candidate dynamic fields; an LLM classifies semantic type.
"""

from __future__ import annotations

import difflib
from typing import Any

from docx import Document

try:
    from utils.style_extractor import iter_body_blocks
except ImportError:
    from style_extractor import iter_body_blocks


def doc_to_segments(doc: Document, max_paragraphs: int = 500) -> list[dict[str, Any]]:
    """
    Normalize a DOCX into a list of segments for structural diff.
    Each segment: segment_index (para_id), section (hint), text.
    """
    segments = []
    for i, (para, _tid, _r, _c) in enumerate(iter_body_blocks(doc)):
        if i >= max_paragraphs:
            break
        text = (para.text or "").strip()
        section = _segment_section_hint(text, i)
        segments.append({
            "segment_index": i,
            "section": section,
            "text": text,
        })
    return segments


def _segment_section_hint(text: str, index: int) -> str:
    """Return a short section label for alignment and context."""
    t = (text or "").strip().lower()
    if not t:
        return "continuation"
    if "supreme court" in t or "county of" in t[:30]:
        return "court_header"
    if "plaintiff" in t and ("against" in t or len(t) < 80):
        return "caption"
    if "against" in t and len(t) < 30:
        return "caption"
    if "defendant" in t and len(t) < 80:
        return "caption"
    if "index no" in t or "date filed" in t or "docket" in t:
        return "caption"
    if "basis of venue" in t or "venue is" in t:
        return "venue"
    if "wherefore" in t:
        return "wherefore"
    if "as and for" in t and "cause of action" in t:
        return "cause_of_action"
    if "that on" in t or "by reason of" in t or "pursuant to" in t:
        return "body"
    if "attorneys for" in t or "attorney for" in t:
        return "signature_block"
    if "________" in t or (t.strip() and all(c in " _\t" for c in t.strip())):
        return "signature_line"
    return "body"


def diff_segments(
    segments_a: list[dict],
    segments_b: list[dict],
    *,
    min_span_length: int = 1,
    skip_identical_segments: bool = True,
) -> list[dict[str, Any]]:
    """
    Compare two segment lists. Align by segment_index (1:1 by position).
    For each aligned pair where text differs, compute word-level diff and emit
    differing spans (in document A's coordinates).

    Returns list of:
      segment_index, start, end, text_a, text_b, context_before, context_after
    """
    n = min(len(segments_a), len(segments_b))
    results = []
    for i in range(n):
        sa = segments_a[i]
        sb = segments_b[i]
        text_a = sa.get("text") or ""
        text_b = sb.get("text") or ""
        if skip_identical_segments and text_a.strip() == text_b.strip():
            continue
        # Find differing spans within this segment using SequenceMatcher
        matcher = difflib.SequenceMatcher(None, text_a, text_b)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "replace":
                # Differing span in A is [i1:i2]
                if i2 - i1 < min_span_length:
                    continue
                # Trim to avoid pure whitespace
                span_a = text_a[i1:i2]
                span_b = text_b[j1:j2]
                if not span_a.strip() and not span_b.strip():
                    continue
                context_before = text_a[max(0, i1 - 60):i1].replace("\n", " ")
                context_after = text_a[i2:i2 + 60].replace("\n", " ")
                results.append({
                    "segment_index": i,
                    "start": i1,
                    "end": i2,
                    "text_a": span_a.strip(),
                    "text_b": span_b.strip(),
                    "context_before": context_before.strip(),
                    "context_after": context_after.strip(),
                    "section": sa.get("section", "body"),
                })
            elif tag == "delete":
                # Only in A — treat as dynamic span (e.g. optional line)
                if i2 - i1 < min_span_length:
                    continue
                span_a = text_a[i1:i2]
                if not span_a.strip():
                    continue
                context_before = text_a[max(0, i1 - 60):i1].replace("\n", " ")
                context_after = text_a[i2:i2 + 60].replace("\n", " ")
                results.append({
                    "segment_index": i,
                    "start": i1,
                    "end": i2,
                    "text_a": span_a.strip(),
                    "text_b": "",
                    "context_before": context_before.strip(),
                    "context_after": context_after.strip(),
                    "section": sa.get("section", "body"),
                })
            elif tag == "insert":
                # Only in B — we use primary (A) as layout; skip or note
                pass
    return results


def diff_documents(
    doc_a: Document,
    doc_b: Document,
    max_paragraphs: int = 500,
    min_span_length: int = 1,
) -> list[dict[str, Any]]:
    """
    Run full structural diff between two documents.
    Returns list of differing spans (in doc_a coordinates) with text_a, text_b, context.
    """
    segments_a = doc_to_segments(doc_a, max_paragraphs=max_paragraphs)
    segments_b = doc_to_segments(doc_b, max_paragraphs=max_paragraphs)
    return diff_segments(
        segments_a,
        segments_b,
        min_span_length=min_span_length,
        skip_identical_segments=True,
    )
