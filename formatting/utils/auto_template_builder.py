"""
Automatic TemplateBuilder: upload sample DOCX → detect fields → replace with «FIELD_NAME» → validate → save.

Hybrid strategy (default): caption fields from caption block only (deterministic); body fields from body text only (LLM).
Fallback: full-document LLM when hybrid=False. Replacements are run-safe in body, tables, headers, footers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

from docx import Document

from utils.placeholder_docx import (
    document_contains_value,
    paragraph_full_text,
    replace_globally_full,
)
from utils.document_structure import extract_structure


class TemplateValidationError(Exception):
    """Raised when original dynamic values still appear in the document after placeholder replacement."""

    def __init__(self, message: str, remaining: list[tuple[str, str]]):
        super().__init__(message)
        self.remaining = remaining  # [(field_name, exact_value), ...]


FIELD_DETECTION_PROMPT = """Given this legal document text, identify all dynamic values that would change from case to case.

Return a JSON object where each key is a field name (UPPER_SNAKE_CASE) and each value is the EXACT string as it appears in the document. Do not modify spelling, punctuation, or casing.

Include fields such as:
- Party names (plaintiff, defendant)
- Court/case identifiers (index number, county, venue)
- Dates (accident date, filing date, signature date)
- Addresses and contact info (firm address, phone)
- Case-specific details (vehicle make/model, plate number, amounts, locations, etc.)

Example output:
{
  "PLAINTIFF_NAME": "MUMUNI AHMED",
  "DEFENDANT_NAME": "RICHARD MORALES",
  "CASE_COUNTY": "KINGS",
  "INDEX_NO": "12345",
  "ACCIDENT_DATE": "March 10, 2012",
  "VEHICLE_MAKE": "2008 Lexus",
  "PLATE_NUMBER": "W69ATJ",
  "FIRM_ADDRESS": "401 Park Avenue South, 10th Floor, New York, New York 10016"
}

Rules:
- Return ONLY the JSON object. No markdown, no code fence, no explanation.
- Use the exact text as it appears in the document (same casing, same punctuation).
- Omit any field you cannot find; do not guess or invent values.
- Use empty string "" for a field name if the value is empty or not found.

Document text:
---
{text}
---

Return only the JSON object."""


# --- Hybrid: deterministic caption + LLM body-only ---

INDEX_NO_REGEX = re.compile(r"^\s*Index\s+No\.?\s*:?\s*(.*)$", re.I | re.DOTALL)
DATE_FILED_REGEX = re.compile(r"^\s*Date\s+Filed\s*:?\s*(.*)$", re.I | re.DOTALL)


def extract_caption_fields_deterministic(doc: Document, structure: dict[str, Any] | None = None) -> dict[str, str]:
    """
    Extract caption fields from the caption block only (paragraph containing "-against-" and N before/after).
    Uses structure: plaintiff = paragraph immediately before "Plaintiff,", defendant = before "Defendant.",
    index_no / date_filed = value part after label (regex). No LLM. Prevents body confusion (e.g. plaintiff name in allegations).
    """
    if structure is None:
        structure = extract_structure(doc)
    para_list = structure.get("paragraphs") or []
    cap = structure.get("caption") or {}
    result: dict[str, str] = {}

    def _text(i: int) -> str:
        if i < 0 or i >= len(para_list):
            return ""
        return (paragraph_full_text(para_list[i][0]) or "").strip()

    pi = cap.get("plaintiff_index")
    if pi is not None:
        result["PLAINTIFF_NAME"] = _text(pi)
    di = cap.get("defendant_index")
    if di is not None:
        result["DEFENDANT_NAME"] = _text(di)
    ii = cap.get("index_no_index")
    if ii is not None:
        t = _text(ii)
        m = INDEX_NO_REGEX.match(t)
        if m and m.group(1):
            result["INDEX_NO"] = m.group(1).strip()
    dfi = cap.get("date_filed_index")
    if dfi is not None:
        t = _text(dfi)
        m = DATE_FILED_REGEX.match(t)
        if m and m.group(1):
            result["DATE_FILED"] = m.group(1).strip()
    vi = cap.get("venue_index")
    if vi is not None:
        result["PLAINTIFF_RESIDENCE"] = _text(vi)

    return {k: v for k, v in result.items() if v}


def extract_body_text_excluding_caption(doc: Document, structure: dict[str, Any]) -> str:
    """Build plain text from all body paragraphs EXCEPT the caption block (to send only body to LLM)."""
    para_list = structure.get("paragraphs") or []
    cap = structure.get("caption") or {}
    start = cap.get("start", 0)
    end = cap.get("end", 0)
    parts = []
    for i, item in enumerate(para_list):
        if start <= i < end:
            continue
        para = item[0]
        t = paragraph_full_text(para).strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


BODY_FIELDS_DETECTION_PROMPT = """The following text is from a legal document EXCLUDING the caption (party names and court header are already handled).

Extract ONLY dynamic values that appear in the BODY/NARRATIVE: accident date, vehicle make/model, plate number, location/address of incident, amounts, injury descriptions, or other case-specific facts. Return a JSON object: field_name (UPPER_SNAKE_CASE) -> EXACT string as it appears in the text.

Do NOT extract plaintiff name, defendant name, index number, or date filed — those are taken from the caption.

Rules:
- Return ONLY the JSON object. No markdown, no code fence, no explanation.
- Use the exact text as it appears (same casing, punctuation).
- Omit fields you cannot find. Use empty string "" for missing.

Body text:
---
{text}
---

Return only the JSON object."""


def detect_body_fields(body_text: str, llm_callable: Callable[[str], str]) -> dict[str, str]:
    """Send body-only text to LLM; return field_name -> exact string for body/narrative fields only."""
    prompt = BODY_FIELDS_DETECTION_PROMPT.format(text=body_text[:40000])
    raw = llm_callable(prompt)
    return _parse_llm_field_json(raw)


def extract_full_document_text(doc: Document) -> str:
    """Extract plain text from entire document: body, table cells, headers, footers."""
    from utils.placeholder_docx import iter_all_paragraphs_including_headers_footers

    parts = []
    for p in iter_all_paragraphs_including_headers_footers(doc):
        t = paragraph_full_text(p).strip()
        if t:
            parts.append(t)
    return "\n\n".join(parts)


def _parse_llm_field_json(raw: str) -> dict[str, str]:
    """Parse LLM response into dict; strip markdown/code fence."""
    text = (raw or "").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON from field detector: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Field detector did not return a JSON object")
    return {str(k): (str(v) if v is not None else "") for k, v in data.items()}


def detect_dynamic_fields(
    document_text: str,
    llm_callable: Callable[[str], str],
) -> dict[str, str]:
    """
    Send document text to LLM; return dict mapping field_name -> exact string found in document.
    Keys are normalized to UPPER_SNAKE_CASE; values are the exact strings to replace.
    """
    prompt = FIELD_DETECTION_PROMPT.format(text=document_text[:50000])
    raw = llm_callable(prompt)
    return _parse_llm_field_json(raw)


def placeholder_token(field_name: str) -> str:
    """Return the placeholder token for a field (e.g. «PLAINTIFF_NAME»)."""
    return "«" + field_name.strip() + "»"


def build_auto_template(
    doc_path: str | Path,
    output_path: str | Path,
    field_map: dict[str, str] | None = None,
    llm_callable: Callable[[str], str] | None = None,
    hybrid: bool = True,
) -> dict[str, Any]:
    """
    Load DOCX, replace each dynamic value with «FIELD_NAME», validate, save.

    If field_map is None and hybrid=True (default):
      - Pre-locate caption block (paragraph containing "-against-" + N before/after).
      - Extract caption fields deterministically: PLAINTIFF_NAME, DEFENDANT_NAME, INDEX_NO, DATE_FILED, PLAINTIFF_RESIDENCE.
      - Extract body text excluding caption; send to LLM for body-only fields (accident date, vehicle, plate, location, etc.).
      - Merge: caption fields + body fields (caption never overwritten by LLM).
    If field_map is None and hybrid=False: full-document LLM detection (original behavior).
    If field_map is provided, it is used as-is.

    Replaces in: paragraphs, table cells, headers, footers. Paragraph-level (run-safe).
    After replacement, validates no original value remains; else raises TemplateValidationError.
    Returns dict with keys: output_path, field_map, replacement_counts.
    """
    doc_path = Path(doc_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document(doc_path)

    if field_map is None:
        if not callable(llm_callable):
            raise ValueError("Either field_map or llm_callable must be provided")
        if hybrid:
            structure = extract_structure(doc)
            caption_fields = extract_caption_fields_deterministic(doc, structure)
            body_text = extract_body_text_excluding_caption(doc, structure)
            body_fields = detect_body_fields(body_text, llm_callable)
            # Caption first; body adds only keys not in caption (never overwrite plaintiff/defendant from body)
            field_map = dict(caption_fields)
            for k, v in body_fields.items():
                if k not in field_map and v:
                    field_map[k] = v
        else:
            text = extract_full_document_text(doc)
            field_map = detect_dynamic_fields(text, llm_callable)

    # Normalize keys to UPPER_SNAKE_CASE; skip empty values for replace
    field_map = {str(k).strip().upper().replace(" ", "_"): (v if v is not None else "") for k, v in field_map.items()}
    field_map = {k: str(v).strip() if v else "" for k, v in field_map.items()}

    # Replace longer values first so substrings (e.g. "John Doe" before "John") are handled correctly
    items = [(k, v) for k, v in field_map.items() if v]
    items.sort(key=lambda x: -len(x[1]))

    replacement_counts: dict[str, int] = {}
    for field_name, exact_value in items:
        token = placeholder_token(field_name)
        n = replace_globally_full(doc, exact_value, token)
        if n > 0:
            replacement_counts[field_name] = n

    # Validate: no original value may remain
    remaining: list[tuple[str, str]] = []
    for field_name, exact_value in field_map.items():
        if not exact_value:
            continue
        if document_contains_value(doc, exact_value):
            remaining.append((field_name, exact_value))

    if remaining:
        raise TemplateValidationError(
            "Template validation failed: original dynamic values still appear in the document. "
            "Replacements may be split across runs or differ in casing/whitespace.",
            remaining=remaining,
        )

    doc.save(output_path)
    return {
        "output_path": str(output_path),
        "field_map": field_map,
        "replacement_counts": replacement_counts,
    }


class AutoTemplateBuilder:
    """
    One-shot automatic template builder: load sample DOCX, detect fields via LLM, replace with «FIELD_NAME», validate, save.
    """

    def __init__(self, llm_callable: Callable[[str], str] | None = None):
        self.llm_callable = llm_callable

    def build(
        self,
        sample_docx_path: str | Path,
        output_path: str | Path | None = None,
        field_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """
        Build auto_generated_template.docx from sample. If output_path is None, saves next to sample with name auto_generated_template.docx.
        """
        sample_docx_path = Path(sample_docx_path)
        if output_path is None:
            output_path = sample_docx_path.parent / "auto_generated_template.docx"
        return build_auto_template(
            sample_docx_path,
            output_path,
            field_map=field_map,
            llm_callable=self.llm_callable,
        )
