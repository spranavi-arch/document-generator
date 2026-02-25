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

    # No extra keys: allow only schema keys or parent keys of dotted schema keys (e.g. SIGNATURE_BLOCK)
    allowed = set(placeholders)
    for pk in placeholders:
        if "." in pk:
            allowed.add(pk.split(".")[0])
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
