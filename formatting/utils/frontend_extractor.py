"""
Layout-Aware Mapping: extract structured fields from raw frontend text via LLM.

Input: raw frontend text (paste, notes, case narrative).
Output: structured JSON only (plaintiff, defendant, allegations, etc.).
No formatted output, no markdown, no explanation.
"""

from __future__ import annotations

import json
import re


# Default schema keys for summons/complaint (injector uses these keys)
FRONTEND_EXTRACT_SCHEMA = {
    "plaintiff": "",
    "defendant": "",
    "index_no": "",
    "date_filed": "",
    "plaintiff_residence": "",
    "allegations": [],
    "attorney_name": "",
    "firm_name": "",
    "firm_address": "",
    "phone": "",
    "signature_date": "",
    "notice_of_entry": "",
    "notice_of_settlement": "",
}


def build_extraction_prompt(raw_text: str, schema_keys: dict | None = None) -> str:
    """Build prompt for LLM: extract structured fields from raw text. Return JSON only."""
    keys_hint = schema_keys or FRONTEND_EXTRACT_SCHEMA
    keys_desc = ", ".join(keys_hint.keys())
    return f"""Extract structured data from the following legal document text. Return a single JSON object with only these keys. Return JSON only — no markdown, no code fence, no explanation.

Keys to extract: {keys_desc}

Guidance:
- plaintiff: Plaintiff's full name as in caption (e.g. "JOHN DOE," or "JOHN DOE").
- defendant: Defendant's full name as in caption (e.g. "JANE SMITH," or "JANE SMITH").
- index_no: The index/docket number after "Index No.:" (e.g. "NNHCV216111723S").
- date_filed: Date after "Date Filed:" (YYYY-MM-DD or as shown).
- plaintiff_residence: Venue basis sentence (e.g. "Plaintiff's Residence: 1070 Amity Road...").
- allegations: Array of strings; each string is one numbered allegation paragraph (e.g. "1. At the time of the accident...").
- attorney_name: Attorney name with title (e.g. "MICHAEL COHAN, ESQ.").
- firm_name: Law firm name (e.g. "COHAN LAW FIRM PLLC").
- firm_address: Full address (one or two lines combined).
- phone: Phone number.
- signature_date: Date in signature block (e.g. "Dated: February 8, 2024").
- notice_of_entry: Text of NOTICE OF ENTRY if present.
- notice_of_settlement: Text of NOTICE OF SETTLEMENT if present.

Use empty string "" for missing string fields. Use [] for allegations if none. No extra keys.

Input text:
---
{raw_text.strip()}
---

Return only the JSON object."""


def parse_extraction_response(raw: str) -> dict:
    """Parse LLM response: strip markdown/code fence, return dict. Raises on invalid JSON."""
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
        raise ValueError(f"Invalid JSON from extractor: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("Extractor did not return a JSON object")
    return data


def normalize_extracted_to_injector(data: dict) -> dict:
    """
    Map frontend extractor keys (snake_case) to injector keys (UPPER_SNAKE or schema).
    Ensures allegations list and string fields.
    """
    mapping = {
        "plaintiff": "PLAINTIFF_NAME",
        "defendant": "DEFENDANT_NAME",
        "index_no": "INDEX_NO",
        "date_filed": "DATE_FILED",
        "plaintiff_residence": "PLAINTIFF_RESIDENCE",
        "allegations": "CAUSE_OF_ACTION_1_PARAGRAPHS",
        "attorney_name": "ATTORNEY_NAME",
        "firm_name": "FIRM_NAME",
        "firm_address": "FIRM_ADDRESS",
        "phone": "PHONE",
        "signature_date": "SIGNATURE_DATE",
        "notice_of_entry": "NOTICE_OF_ENTRY",
        "notice_of_settlement": "NOTICE_OF_SETTLEMENT",
    }
    out = {}
    for src_key, dest_key in mapping.items():
        val = data.get(src_key)
        if val is None:
            out[dest_key] = [] if dest_key == "CAUSE_OF_ACTION_1_PARAGRAPHS" else ""
        elif isinstance(val, list):
            out[dest_key] = [str(x) for x in val]
        else:
            out[dest_key] = str(val)
    return out


class FrontendTextExtractor:
    """LLM-based extractor: raw frontend text -> structured JSON fields."""

    def __init__(self, llm_callable=None):
        """
        llm_callable: (prompt: str) -> str. If None, extract() returns empty schema (for testing).
        """
        self.llm_callable = llm_callable

    def extract(self, raw_text: str, *, normalize: bool = True) -> dict:
        """
        Extract structured fields from raw text. Returns dict with snake_case keys if normalize=False,
        else dict with UPPER_SNAKE keys suitable for LayoutAwareInjector.
        """
        if not (raw_text or "").strip():
            base = dict(FRONTEND_EXTRACT_SCHEMA)
            return normalize_extracted_to_injector(base) if normalize else base
        if not callable(self.llm_callable):
            base = dict(FRONTEND_EXTRACT_SCHEMA)
            return normalize_extracted_to_injector(base) if normalize else base
        prompt = build_extraction_prompt(raw_text)
        raw = self.llm_callable(prompt)
        data = parse_extraction_response(raw)
        return normalize_extracted_to_injector(data) if normalize else data
