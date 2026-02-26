"""
Fill a template DOCX with JSON data: validate against schema, merge scalars (and blocks in M3).

Entry: fill_template(template_path, json_data, output_path, schema_path)
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

try:
    from utils.placeholder_docx import replace_placeholders
    from utils.style_extractor import iter_body_blocks
    from utils.formatter import force_legal_run_format
except ImportError:
    from placeholder_docx import replace_placeholders
    from style_extractor import iter_body_blocks
    from formatter import force_legal_run_format


def load_schema(schema_path: str | Path) -> dict[str, Any]:
    """Load schema.json from path."""
    with open(schema_path, encoding="utf-8") as f:
        return json.load(f)


def _get_nested(data: dict[str, Any], dotted_key: str) -> Any:
    """Get value from nested dict using dotted key, e.g. SIGNATURE_BLOCK.FIRM."""
    parts = dotted_key.split(".")
    cur: Any = data
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def validate_json_against_schema(data: dict[str, Any], schema: dict[str, Any]) -> None:
    """
    Validate required keys present, types correct, no extra keys.
    Raises ValueError with a clear message on failure.
    """
    placeholders = schema.get("placeholders") or {}
    errors: list[str] = []

    for key, spec in placeholders.items():
        if not isinstance(spec, dict):
            continue
        required = spec.get("required", False)
        ptype = spec.get("type", "string")
        val = _get_nested(data, key)
        if val is None:
            if required:
                errors.append(f"Missing required key: {key}")
            continue
        if ptype == "string" and not isinstance(val, str):
            errors.append(f"{key}: expected string, got {type(val).__name__}")
        elif ptype == "date":
            if not isinstance(val, str):
                errors.append(f"{key}: expected date string, got {type(val).__name__}")
            # Basic sanity: allow YYYY-MM-DD or similar
        elif ptype == "string_list":
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                errors.append(f"{key}: expected list of strings, got {type(val).__name__}")
        elif ptype == "object":
            if not isinstance(val, dict):
                errors.append(f"{key}: expected object, got {type(val).__name__}")
        if required and val in (None, "", []):
            errors.append(f"Required key {key} is empty")

    # No extra keys: allow only schema keys or parent keys of dotted schema keys (e.g. SIGNATURE_BLOCK),
    # plus common legal keys that extraction may add even when the schema omits them (e.g. template from different sample).
    allowed = set(placeholders)
    for pk in placeholders:
        if "." in pk:
            allowed.add(pk.split(".")[0])
    allowed |= {
        "PLAINTIFF_NAME",
        "DEFENDANT_NAME",
        "INDEX_NO",
        "DATE_FILED",
        "VENUE_BASIS",
        "WHEREFORE",
        "CAUSE_OF_ACTION_1_TITLE",
        "CAUSE_OF_ACTION_1_PARAS__BLOCK",
        "SIGNATURE_BLOCK",
    }
    for key in data:
        if key not in allowed:
            errors.append(f"Unexpected key: {key}")

    if errors:
        raise ValueError("Validation failed: " + "; ".join(errors))


def _flatten_value(value: Any, prefix: str) -> dict[str, str]:
    """Recursively flatten nested dict to dotted keys and string values."""
    out: dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            new_prefix = f"{prefix}.{k}" if prefix else k
            out.update(_flatten_value(v, new_prefix))
    elif value is not None and not isinstance(value, (list, dict)):
        out[prefix] = str(value)
    return out


def flatten_json_to_replacements(
    data: dict[str, Any],
    schema: dict[str, Any],
) -> dict[str, str]:
    """
    Build replacements dict for scalar placeholders only.
    Nested objects (e.g. SIGNATURE_BLOCK) become SIGNATURE_BLOCK.FIRM etc.
    Keys with render "paragraphs" (__BLOCK) are excluded; they are handled in block merge.
    """
    placeholders = schema.get("placeholders") or {}
    replacements: dict[str, str] = {}

    for key, spec in placeholders.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("render") == "paragraphs":
            continue  # Block placeholder; handled in merge_blocks
        val = _get_nested(data, key)
        if val is None:
            continue
        if isinstance(val, dict):
            flat = _flatten_value(val, key)
            for dotted, str_val in flat.items():
                replacements[f"{{{{{dotted}}}}}"] = str_val
        elif not isinstance(val, list):
            replacements[f"{{{{{key}}}}}"] = str(val)

    return replacements


def _block_placeholders_from_data(data: dict[str, Any], schema: dict[str, Any]) -> dict[str, list[str]]:
    """Extract keys with render 'paragraphs' from schema; return dict of key -> list of strings."""
    placeholders = schema.get("placeholders") or {}
    out: dict[str, list[str]] = {}
    for key, spec in placeholders.items():
        if not isinstance(spec, dict) or spec.get("render") != "paragraphs":
            continue
        val = _get_nested(data, key)
        if isinstance(val, list) and val:
            out[key] = [str(x) for x in val]
    return out


def _create_paragraph_with_text(doc: Document, source_para: Paragraph, text: str) -> Any:
    """Create a new w:p element with cloned pPr from source_para and one run with text."""
    new_p = OxmlElement("w:p")
    # Clone paragraph properties from source
    src_p = source_para._element
    pPr = src_p.find(qn("w:pPr"))
    if pPr is not None:
        new_pPr = copy.deepcopy(pPr)
        new_p.append(new_pPr)
    # Add run with text
    r = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    r.append(t)
    new_p.append(r)
    return new_p


def merge_blocks(doc: Document, block_placeholders: dict[str, list[str]]) -> None:
    """
    Find each paragraph (body or table cell) containing {{KEY__BLOCK}}, remove it,
    and insert N paragraphs (same style/format) with the list content.
    """
    if not block_placeholders:
        return
    marker_to_key = {f"{{{{{k}}}}}": k for k in block_placeholders}

    # Collect (para, table_id, row, col, marker_found) for paragraphs that contain a block marker
    to_process: list[tuple[Paragraph, str, list[str]]] = []
    for para, table_id, row, col in iter_body_blocks(doc):
        text = para.text or ""
        for marker, key in marker_to_key.items():
            if marker in text:
                to_process.append((para, key, block_placeholders[key]))
                break

    for para, key, lines in to_process:
        parent = para._element.getparent()
        if parent is None:
            continue
        # Create N new paragraph elements
        new_elements = [_create_paragraph_with_text(doc, para, line) for line in lines]
        if not new_elements:
            # Remove placeholder para and leave nothing
            parent.remove(para._element)
            continue
        # Find index of para in parent's children (only w:p children for body; for tc, all direct children)
        children = list(parent.iterchildren())
        try:
            idx = children.index(para._element)
        except ValueError:
            continue
        parent.remove(para._element)
        for i, new_el in enumerate(new_elements):
            parent.insert(idx + i, new_el)


def fill_template(
    template_path: str | Path,
    json_data: dict[str, Any],
    output_path: str | Path,
    schema_path: str | Path,
) -> None:
    """
    Load template and schema, validate JSON, merge scalar placeholders, then block placeholders, save.
    """
    schema = load_schema(schema_path)
    validate_json_against_schema(json_data, schema)
    replacements = flatten_json_to_replacements(json_data, schema)
    doc = Document(template_path)
    replace_placeholders(doc, replacements)
    block_placeholders = _block_placeholders_from_data(json_data, schema)
    merge_blocks(doc, block_placeholders)
    # Run normalization: black color, no accidental italics (body + table cells)
    for para, _tid, _r, _c in iter_body_blocks(doc):
        force_legal_run_format(para)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)


def build_extract_fields_prompt(schema: dict[str, Any], input_text: str) -> str:
    """
    Build prompt for LLM: extract schema fields from the given text and return JSON only.
    Includes explicit legal-document guidance so plaintiff/defendant names and other fields are found.
    """
    placeholders = schema.get("placeholders") or {}
    doc_type = schema.get("doc_type", "SummonsAndComplaint")
    lines = [
        f"You are extracting structured data from a {doc_type} legal document. The text may be a summons, complaint, or similar filing.",
        "Extract the following fields from the text below. Return a single JSON object with only these keys. Return JSON only — no markdown, no explanation, no code fence.",
        "",
        "Fields to extract (key: type). Where to find them in the document:",
    ]
    # Add brief hints for common keys so the model knows what to look for
    hints = {
        "PLAINTIFF_NAME": "Plaintiff's full name as in the caption, usually ALL CAPS before 'Plaintiff,' or 'Plaintiff.' (e.g. KELLEY SKAARVA). Use the exact spelling from the text.",
        "DEFENDANT_NAME": "Defendant's full name as in the caption, usually ALL CAPS before 'Defendant.' or 'Defendant' (e.g. HENRY SARBIESKI). Use the exact spelling from the text.",
        "INDEX_NO": "The index/docket number after 'Index No.:' or 'INDEX NO.:' (e.g. NNHCV216111723S).",
        "DATE_FILED": "The date after 'Date Filed:' or 'DATE FILED:'. Use YYYY-MM-DD if a full date is given, or keep placeholder like [Date] if that appears.",
        "VENUE_BASIS": "The sentence or phrase stating basis of venue (e.g. 'Plaintiff's Residence: 1070 Amity Road, Lot 52, in Bethany, New Haven County, Connecticut.').",
        "WHEREFORE": "The WHEREFORE paragraph if present (prayer for relief).",
        "CAUSE_OF_ACTION_1_TITLE": "The cause of action heading in ALL CAPS (e.g. NEGLIGENCE), usually after 'AS AND FOR A FIRST CAUSE OF ACTION:'.",
        "CAUSE_OF_ACTION_1_PARAS__BLOCK": "Array of the numbered allegation paragraphs (1., 2., 3., ...) under that cause of action. Each item is one full paragraph text. Preserve numbering in the text if present.",
        "SIGNATURE_BLOCK.FIRM": "Law firm name (e.g. COHAN LAW FIRM PLLC, MARKEY BARRETT, PC).",
        "SIGNATURE_BLOCK.ATTORNEY": "Attorney name with title (e.g. MICHAEL COHAN, ESQ., PETER G. BARRETT, ESQ.).",
        "SIGNATURE_BLOCK.PHONE": "Phone number in (xxx) xxx-xxxx form.",
        "SIGNATURE_BLOCK.ADDRESS_LINE_1": "First line of attorney/firm address (street, suite, floor).",
        "SIGNATURE_BLOCK.ADDRESS_LINE_2": "Second line (city, state zip).",
    }
    for key, spec in placeholders.items():
        if not isinstance(spec, dict):
            continue
        ptype = spec.get("type", "string")
        required = "required" if spec.get("required", False) else "optional"
        hint = hints.get(key, "")
        if hint:
            lines.append(f"  - {key}: {ptype} ({required}) — {hint}")
        else:
            lines.append(f"  - {key}: {ptype} ({required})")
    lines.extend([
        "",
        "Rules:",
        "- No extra keys. Use empty string \"\" or [] for missing optional fields.",
        "- CRITICAL: You MUST extract PLAINTIFF_NAME and DEFENDANT_NAME. Look for ALL CAPS names in the caption: the name on the line before 'Plaintiff,' or 'Plaintiff.' is PLAINTIFF_NAME; the name before 'Defendant.' or 'Defendant' is DEFENDANT_NAME. Example: 'KELLEY SKAARVA,' and 'HENRY SARBIESKI,'.",
        "- For PLAINTIFF_NAME and DEFENDANT_NAME, copy the name exactly as it appears in the caption (including trailing comma if present, e.g. 'KELLEY SKAARVA,').",
        "- For string_list (e.g. CAUSE_OF_ACTION_1_PARAS__BLOCK), use an array of strings; each string is one paragraph (include the number and text, e.g. '1. At the time of the accident...').",
        "- For nested keys (SIGNATURE_BLOCK.*), use a nested object: SIGNATURE_BLOCK: { FIRM: \"...\", ATTORNEY: \"...\", PHONE: \"...\", ADDRESS_LINE_1: \"...\", ADDRESS_LINE_2: \"...\" }.",
        "- Dates: use YYYY-MM-DD when a full date is given; otherwise keep placeholders like [Date] or [February 8, 2024] if that appears in the text.",
        "- Search the entire text: captions may appear more than once (e.g. at top of summons and again on complaint). Use the first clear occurrence for index/date/parties; use the attorney block that matches the main signature (e.g. COHAN LAW FIRM PLLC / MICHAEL COHAN for the summons cover).",
        "",
        "Example shape (replace with actual values from the text):",
        '  {"INDEX_NO": "NNHCV216111723S", "DATE_FILED": "[Date]", "PLAINTIFF_NAME": "KELLEY SKAARVA,", "DEFENDANT_NAME": "HENRY SARBIESKI,", "VENUE_BASIS": "Plaintiff\'s Residence: 1070 Amity Road...", "SIGNATURE_BLOCK": {"FIRM": "COHAN LAW FIRM PLLC", "ATTORNEY": "MICHAEL COHAN, ESQ.", "PHONE": "(855) 855-0321", "ADDRESS_LINE_1": "401 Park Avenue South, 10th Floor", "ADDRESS_LINE_2": "New York, New York 10016"}, ...}',
        "",
        "Input text:",
        "---",
        input_text.strip(),
        "---",
        "",
        "Return only the JSON object, no other text.",
    ])
    return "\n".join(lines)


def build_template_fill_prompt(schema: dict[str, Any], case_facts: str) -> str:
    """
    Build prompt for LLM: return JSON only matching the schema from case facts/KB.
    Instruct: no headers, no repeated caption, no new sections.
    """
    placeholders = schema.get("placeholders") or {}
    doc_type = schema.get("doc_type", "SummonsAndComplaint")
    lines = [
        f"Generate a single JSON object that fills the placeholders for a {doc_type} document.",
        "Use only the following keys and types. Return JSON only — no markdown, no explanation, no headers.",
        "",
        "Placeholders (key: type, required):",
    ]
    for key, spec in placeholders.items():
        if not isinstance(spec, dict):
            continue
        ptype = spec.get("type", "string")
        required = spec.get("required", False)
        req = "required" if required else "optional"
        lines.append(f"  - {key}: {ptype} ({req})")
    lines.extend([
        "",
        "Rules:",
        "- No extra keys. No repeated caption or new sections.",
        "- For string_list (e.g. CAUSE_OF_ACTION_1_PARAS__BLOCK), provide an array of paragraph strings.",
        "- For nested keys (e.g. SIGNATURE_BLOCK.FIRM), you may use a nested object: SIGNATURE_BLOCK: { FIRM: \"...\", ATTORNEY: \"...\" }.",
        "- Dates: use YYYY-MM-DD or the format expected by the court.",
        "",
        "Case facts / knowledge:",
        case_facts.strip(),
        "",
        "Return only the JSON object, no other text.",
    ])
    return "\n".join(lines)


def parse_llm_json_response(raw: str, schema: dict[str, Any]) -> dict[str, Any]:
    """
    Extract JSON from LLM response (strip markdown/code fences if present), validate against schema, return data.
    Raises ValueError on parse or validation failure.
    """
    text = raw.strip()
    # Strip optional markdown code block
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON from LLM: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM did not return a JSON object")
    validate_json_against_schema(data, schema)
    return data
