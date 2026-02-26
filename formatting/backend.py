"""
Backend for the template workflow only.

Current approach: sample → template + schema → fill template with JSON.
Extract fields from input text via LLM (optional).
Two-sample: optional diff-based template mining (structural diff + LLM classification).
"""

import json
import os
import re
import tempfile

from docx import Document

from utils.structural_diff import diff_documents
from utils.style_extractor import _paragraph_has_bottom_border
from utils.template_builder import (
    build_schema_from_placeholders,
    detect_placeholder_keys,
    sample_to_template,
    save_schema,
)
from utils.two_sample_blueprint import (
    apply_placeholders_to_docx as blueprint_apply_placeholders,
    infer_placeholders_from_two_docx,
)
from utils.template_filler import (
    build_extract_fields_prompt,
    fill_template as _fill_template,
    load_schema,
    parse_llm_json_response,
    validate_json_against_schema,
)


# Semantic field names we suggest to the LLM (controlled vocabulary; LLM may suggest another UPPER_SNAKE_CASE)
DIFF_FIELD_NAMES = (
    "PLAINTIFF_NAME",
    "DEFENDANT_NAME",
    "INDEX_NO",
    "DATE_FILED",
    "VENUE_BASIS",
    "COUNTY",
    "ACCIDENT_DATE",
    "VEHICLE_YEAR",
    "VEHICLE_MAKE",
    "LICENSE_PLATE",
    "ADDRESS_LINE_1",
    "ADDRESS_LINE_2",
    "SIGNATURE_BLOCK.FIRM",
    "SIGNATURE_BLOCK.ATTORNEY",
    "SIGNATURE_BLOCK.PHONE",
    "SIGNATURE_BLOCK.ADDRESS_LINE_1",
    "SIGNATURE_BLOCK.ADDRESS_LINE_2",
    "WHEREFORE",
    "CAUSE_OF_ACTION_1_TITLE",
)


def build_classify_span_prompt(
    text_a: str,
    text_b: str,
    context_before: str = "",
    context_after: str = "",
    section: str = "body",
) -> str:
    """Build prompt for LLM to classify a differing span into a semantic field name."""
    context = f"Before: \"{context_before}\" ... [DIFFERING TEXT] ... \"{context_after}\""
    return f"""Two structurally identical legal documents have different text in the same position.

Sample 1 text: "{text_a}"
Sample 2 text: "{text_b}"
Section hint: {section}
Context: {context}

Assign exactly one semantic field name for this slot. Use one of these when it fits, otherwise respond with a new UPPER_SNAKE_CASE name:
{", ".join(DIFF_FIELD_NAMES)}

Reply with only the field name, e.g. PLAINTIFF_NAME or DEFENDANT_NAME. No explanation."""


def _classify_span_fallback(diff_item: dict) -> str:
    """Heuristic fallback when LLM is unavailable or fails."""
    text_a = (diff_item.get("text_a") or "").lower()
    text_b = (diff_item.get("text_b") or "").lower()
    ctx_before = (diff_item.get("context_before") or "").lower()
    ctx_after = (diff_item.get("context_after") or "").lower()
    section = (diff_item.get("section") or "").lower()
    combined = f"{ctx_before} {ctx_after} {section}"
    if "plaintiff" in combined and ("against" in ctx_before or "plaintiff" in ctx_before):
        return "PLAINTIFF_NAME"
    if "defendant" in combined:
        return "DEFENDANT_NAME"
    if "index" in combined or "index no" in combined:
        return "INDEX_NO"
    if "date filed" in combined or "filed" in combined:
        return "DATE_FILED"
    if "venue" in combined or "basis" in combined:
        return "VENUE_BASIS"
    if "county" in combined:
        return "COUNTY"
    if "vehicle" in combined or "license" in combined or "plate" in combined:
        if re.search(r"\b(19|20)\d{2}\b", text_a + text_b):
            return "VEHICLE_YEAR"
        if any(x in text_a + text_b for x in ["lexus", "toyota", "honda", "ford", "chevrolet"]):
            return "VEHICLE_MAKE"
        return "LICENSE_PLATE"
    if "address" in combined or "road" in combined or "avenue" in combined:
        return "ADDRESS_LINE_1"
    if "attorney" in combined or "esq" in combined:
        return "SIGNATURE_BLOCK.ATTORNEY"
    if "firm" in combined or "pllc" in combined or "p.c." in combined:
        return "SIGNATURE_BLOCK.FIRM"
    if "phone" in combined or re.search(r"\(\d{3}\)", text_a + text_b):
        return "SIGNATURE_BLOCK.PHONE"
    if "wherefore" in combined:
        return "WHEREFORE"
    if "cause of action" in combined or "as and for" in combined:
        return "CAUSE_OF_ACTION_1_TITLE"
    if re.search(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4}\b", text_a + text_b, re.I):
        return "ACCIDENT_DATE"
    return "CUSTOM_FIELD"


def classify_differing_span(diff_item: dict, llm_callable=None) -> str:
    """
    Classify a single differing span into a semantic field name.
    If llm_callable is provided, call it with the prompt; otherwise use heuristic fallback.
    llm_callable(prompt: str) -> str should return a single line with the field name.
    """
    prompt = build_classify_span_prompt(
        text_a=diff_item.get("text_a", ""),
        text_b=diff_item.get("text_b", ""),
        context_before=diff_item.get("context_before", ""),
        context_after=diff_item.get("context_after", ""),
        section=diff_item.get("section", "body"),
    )
    if callable(llm_callable):
        try:
            raw = llm_callable(prompt)
            name = (raw or "").strip().splitlines()[0].strip().upper().replace(" ", "_")
            if name and re.match(r"^[A-Z][A-Z0-9_\.]*$", name):
                return name
        except Exception:
            pass
    return _classify_span_fallback(diff_item)


def get_diff_based_candidates(doc_primary: Document, doc_secondary: Document, llm_callable=None):
    """
    Run structural diff between two docs, then classify each differing span via LLM (or fallback).
    Returns list of dicts: para_id (segment_index), start_char, end_char, placeholder_key.
    """
    diffs = diff_documents(doc_primary, doc_secondary, max_paragraphs=500, min_span_length=1)
    candidates = []
    for d in diffs:
        key = classify_differing_span(d, llm_callable=llm_callable)
        candidates.append({
            "para_id": d["segment_index"],
            "start_char": d["start"],
            "end_char": d["end"],
            "placeholder_key": key,
        })
    return candidates


def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


def get_document_preview_text(docx_path: str) -> str:
    """Build a plain-text preview of a DOCX. Paragraphs with only a bottom border are emitted as [SECTION_UNDERLINE]."""
    doc = Document(docx_path)
    lines = []
    for para in doc.paragraphs:
        text = (para.text or "").strip()
        if not text and _paragraph_has_bottom_border(para):
            lines.append("[SECTION_UNDERLINE]")
        else:
            lines.append(text if text else "")
    return "\n\n".join(lines).strip()


def build_template_from_sample(
    sample_file,
    doc_type: str = "SummonsAndComplaint",
    secondary_file=None,
):
    """
    Build template.docx and schema.json from a primary sample DOCX.
    Optionally run detection on a secondary sample and merge placeholder keys into the schema
    (template layout stays from primary).

    Returns (template_path, schema_path, placeholder_keys, validation_info).
    validation_info is None if no secondary_file; otherwise dict with:
      only_in_primary, only_in_secondary, merged (all sorted key lists).
    """
    project_dir = _project_dir()
    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)

    def to_path(file_obj):
        if hasattr(file_obj, "read"):
            file_obj.seek(0)
            data = file_obj.read()
            file_obj.seek(0)
            tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
            tmp.write(data)
            tmp.flush()
            return tmp.name, True
        return file_obj, False

    primary_path, primary_is_temp = to_path(sample_file)
    secondary_path = None
    secondary_is_temp = False
    try:
        template_path = os.path.join(output_dir, "template.docx")
        schema_path = os.path.join(output_dir, "schema.json")
        doc_primary = Document(primary_path)

        if secondary_file is not None:
            secondary_path, secondary_is_temp = to_path(secondary_file)
            # Deterministic diff + LLM classification pipeline (two_sample_blueprint)
            try:
                llm_callable = _call_llm_for_extraction
            except Exception:
                llm_callable = None
            blueprint = infer_placeholders_from_two_docx(
                primary_path,
                secondary_path,
                document_type=doc_type,
                use_llm=callable(llm_callable),
                llm_callable=llm_callable,
                filter_boilerplate=True,
                context_chars=30,
            )
            doc_a = blueprint["doc_a"]
            units_a = blueprint["units_a"]
            span_diffs_raw = blueprint["span_diffs_raw"]
            llm_map = blueprint["llm_map"]
            placeholder_keys = list(blueprint["placeholder_keys"])
            blueprint_apply_placeholders(doc_a, units_a, span_diffs_raw, llm_map)
            doc_a.save(template_path)
            placeholder_keys = list(blueprint["placeholder_keys"])
            doc_secondary = Document(secondary_path)
            keys_secondary = detect_placeholder_keys(doc_secondary, doc_type=doc_type)
            only_primary = sorted(set(placeholder_keys) - set(keys_secondary))
            only_secondary = sorted(set(keys_secondary) - set(placeholder_keys))
            merged_keys = sorted(set(placeholder_keys) | set(keys_secondary))
            schema = build_schema_from_placeholders(merged_keys, doc_type=doc_type)
            save_schema(schema, schema_path)
            validation_info = {
                "only_in_primary": only_primary,
                "only_in_secondary": only_secondary,
                "merged": merged_keys,
            }
        else:
            placeholder_keys, schema = sample_to_template(
                primary_path, template_path, schema_path, doc_type=doc_type
            )
            merged_keys = list(placeholder_keys)
            validation_info = None

        return template_path, schema_path, merged_keys, validation_info
    finally:
        if primary_is_temp and primary_path and os.path.isfile(primary_path):
            try:
                os.unlink(primary_path)
            except OSError:
                pass
        if secondary_is_temp and secondary_path and os.path.isfile(secondary_path):
            try:
                os.unlink(secondary_path)
            except OSError:
                pass


def fill_template_from_json(template_path: str, schema_path: str, json_data: dict, output_filename: str = "filled_output.docx"):
    """
    Fill template with JSON data and save to output. Returns path to saved DOCX.
    """
    project_dir = _project_dir()
    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_filename)
    _fill_template(template_path, json_data, output_path, schema_path)
    return output_path


def _call_llm_for_extraction(prompt: str) -> str:
    """Call OpenAI or Azure OpenAI with the given prompt; return assistant content."""
    try:
        from openai import AzureOpenAI, OpenAI
    except ImportError:
        raise RuntimeError("openai package is required for extraction. pip install openai")

    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set AZURE_OPENAI_API_KEY or OPENAI_API_KEY in .env for field extraction."
        )

    model = os.environ.get("FORMATTER_LLM_MODEL") or "gpt-4o-mini"
    if os.environ.get("AZURE_OPENAI_API_KEY"):
        client = AzureOpenAI(
            api_key=os.environ["AZURE_OPENAI_API_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
            azure_endpoint=os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/"),
        )
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or model
        resp = client.chat.completions.create(
            model=deployment,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096,
        )
    else:
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4096,
        )

    if not resp.choices:
        raise RuntimeError("LLM returned no choices")
    return (resp.choices[0].message.content or "").strip()


def extract_fields_from_text(input_text: str, schema_path: str) -> dict:
    """
    Extract schema fields from input text using an LLM. Returns a dict suitable for fill_template_from_json.
    If the LLM omits PLAINTIFF_NAME or DEFENDANT_NAME, a regex fallback is used to extract them from the caption.
    Raises if schema is missing, LLM is not configured, or response fails validation after fallback.
    """
    if not input_text or not input_text.strip():
        raise ValueError("Input text is empty")
    schema = load_schema(schema_path)
    prompt = build_extract_fields_prompt(schema, input_text)
    raw = _call_llm_for_extraction(prompt)
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    try:
        data = json.loads(text)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    # Fallback: extract party names from caption if missing
    _fill_party_names_from_text(data, input_text)
    validate_json_against_schema(data, schema)
    return data


def _fill_party_names_from_text(data: dict, input_text: str) -> None:
    """If PLAINTIFF_NAME or DEFENDANT_NAME are missing or empty, try to extract from caption using regex."""
    # Line ending with comma, mostly ALL CAPS, followed by "Plaintiff," or "Plaintiff."
    if not data.get("PLAINTIFF_NAME") or not str(data.get("PLAINTIFF_NAME", "")).strip():
        m = re.search(
            r"([A-Z][A-Z\s,\.\']+)\s*\n\s*Plaintiff[\s,\.]",
            input_text,
            re.IGNORECASE | re.MULTILINE,
        )
        if m:
            data["PLAINTIFF_NAME"] = m.group(1).strip().rstrip(",") or m.group(1).strip()
            if not data["PLAINTIFF_NAME"].endswith(","):
                data["PLAINTIFF_NAME"] += ","

    if not data.get("DEFENDANT_NAME") or not str(data.get("DEFENDANT_NAME", "")).strip():
        m = re.search(
            r"([A-Z][A-Z\s,\.\']+)\s*\n\s*Defendant[\s\.]",
            input_text,
            re.IGNORECASE | re.MULTILINE,
        )
        if m:
            data["DEFENDANT_NAME"] = m.group(1).strip().rstrip(",") or m.group(1).strip()
            if not data["DEFENDANT_NAME"].endswith(","):
                data["DEFENDANT_NAME"] += ","
