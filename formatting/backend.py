"""
Backend for document generation.

Two flows:
- Template flow: sample → template with placeholders → fill with JSON (TemplateFiller).
- Layout-Aware flow: sample DOCX defines layout, frontend text defines content; extract (LLM) → inject (LayoutAwareInjector).
"""

import json
import os
import tempfile

from docx import Document

from utils.schema import SUMMONS_SCHEMA_SPEC, validate_summons_data
from utils.sample_to_template import convert_sample_to_template
from utils.template_filler import fill_template_from_data
from utils.document_structure import DocumentStructureExtractor, extract_structure
from utils.frontend_extractor import FrontendTextExtractor
from utils.layout_injector import inject_content_into_layout
from utils.auto_template_builder import (
    AutoTemplateBuilder,
    TemplateValidationError,
    build_auto_template,
    extract_full_document_text,
)


def _project_dir():
    return os.path.dirname(os.path.abspath(__file__))


def build_template_from_sample(sample_file, output_template_path=None, output_schema_path=None):
    """
    Phase 1: Convert sample DOCX into summons_template.docx with placeholders.
    Returns (template_path, schema_path). Schema is fixed (SUMMONS_SCHEMA_SPEC).
    """
    project_dir = _project_dir()
    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    if hasattr(sample_file, "read"):
        sample_file.seek(0)
        data = sample_file.read()
        sample_file.seek(0)
        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(data)
        tmp.flush()
        sample_path = tmp.name
        cleanup = True
    else:
        sample_path = sample_file
        cleanup = False
    try:
        template_path = output_template_path or os.path.join(output_dir, "summons_template.docx")
        build_counts = convert_sample_to_template(sample_path, template_path)
        if build_counts:
            import logging
            logging.getLogger(__name__).info("Template build replacement counts: %s", build_counts)
        schema_path = output_schema_path or os.path.join(output_dir, "schema.json")
        _write_schema_file(schema_path)
        return template_path, schema_path
    finally:
        if cleanup and os.path.isfile(sample_path):
            try:
                os.unlink(sample_path)
            except OSError:
                pass


def _write_schema_file(schema_path: str) -> None:
    """Write the fixed summons schema to JSON for reference."""
    schema = {
        "doc_type": "SummonsAndComplaint",
        "placeholders": {k: {"type": v, "required": True} for k, v in SUMMONS_SCHEMA_SPEC.items()},
    }
    os.makedirs(os.path.dirname(schema_path), exist_ok=True)
    with open(schema_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)


def get_document_preview_text(docx_path: str) -> str:
    """Plain-text preview of DOCX (paragraphs joined)."""
    doc = Document(docx_path)
    return "\n\n".join((p.text or "").strip() for p in doc.paragraphs).strip()


def generate_json_from_case_facts(case_facts: str, llm_callable=None) -> dict:
    """
    Phase 3: LLM receives case facts + schema, returns JSON only.
    Validate: required keys, types, no extra keys.
    llm_callable(prompt: str) -> str. If None, returns empty schema (for testing).
    """
    prompt = _build_llm_prompt(case_facts)
    if not callable(llm_callable):
        return {k: ([] if v == "string_list" else "") for k, v in SUMMONS_SCHEMA_SPEC.items()}
    raw = llm_callable(prompt)
    data = _parse_llm_json(raw)
    validate_summons_data(data)
    return data


def _build_llm_prompt(case_facts: str) -> str:
    """Build prompt: case facts + schema → return JSON only."""
    keys_desc = ", ".join(SUMMONS_SCHEMA_SPEC.keys())
    return f"""Generate a JSON object that fills the placeholders for a legal summons/complaint.
Use only these keys. Return JSON only — no markdown, no explanation, no code fence.

Keys: {keys_desc}

Types: INDEX_NO, DATE_FILED, PLAINTIFF_NAME, DEFENDANT_NAME, PLAINTIFF_RESIDENCE, SIGNATURE_DATE, ATTORNEY_NAME, FIRM_NAME, FIRM_ADDRESS, PHONE are strings. CAUSE_OF_ACTION_1_PARAGRAPHS is an array of strings (each string is one numbered allegation paragraph).

Rules:
- No extra keys. No other text.
- Use empty string "" for missing string fields. Use [] for CAUSE_OF_ACTION_1_PARAGRAPHS if none.
- Dates: YYYY-MM-DD or court format.

Case facts:
---
{case_facts.strip()}
---

Return only the JSON object."""


def _parse_llm_json(raw: str) -> dict:
    """Extract JSON from LLM response; strip markdown fences."""
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
        raise ValueError(f"Invalid JSON from LLM: {e}") from e
    if not isinstance(data, dict):
        raise ValueError("LLM did not return a JSON object")
    return data


def _call_llm(prompt: str) -> str:
    """Call OpenAI or Azure OpenAI; return assistant content. Used for Phase 3 JSON generation."""
    try:
        from openai import AzureOpenAI, OpenAI
    except ImportError:
        raise RuntimeError("openai package required. pip install openai")
    api_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Set AZURE_OPENAI_API_KEY or OPENAI_API_KEY for LLM.")
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


# --- Layout-Aware flow (no hardcoded placeholders) ---

def extract_from_frontend_text(raw_text: str, llm_callable=None) -> dict:
    """
    Extract structured fields from raw frontend text via LLM. Returns JSON suitable for inject_into_layout.
    llm_callable(prompt: str) -> str. If None, returns empty/default structure.
    """
    extractor = FrontendTextExtractor(llm_callable=llm_callable)
    return extractor.extract(raw_text, normalize=True)


def inject_into_layout(
    sample_docx_path: str,
    data: dict,
    output_filename: str = "layout_filled_output.docx",
    structure: dict | None = None,
) -> str:
    """
    Inject content into the sample DOCX using layout-aware mapping. No placeholders.
    sample_docx_path defines layout; data defines content (from extract_from_frontend_text or manual).
    Allegation count adapts from len(data.get("allegations") or data.get("CAUSE_OF_ACTION_1_PARAGRAPHS") or []).
    Returns path to saved DOCX.
    """
    project_dir = _project_dir()
    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_filename)
    inject_content_into_layout(sample_docx_path, data, output_path, structure=structure)
    return output_path


def get_layout_structure(docx_path: str) -> dict:
    """Extract layout structure from a DOCX. Returns a serializable summary (indices only, no paragraph refs)."""
    extractor = DocumentStructureExtractor.from_path(docx_path)
    full = extractor.extract()
    return {
        "caption": full.get("caption"),
        "allegation_region": full.get("allegation_region"),
        "signature_block": full.get("signature_block"),
        "footer": full.get("footer"),
    }


def fill_template_from_json(
    template_path: str,
    schema_path: str,
    json_data: dict,
    output_filename: str = "final_output.docx",
) -> str:
    """
    Phase 4: Validate JSON and merge into template. Returns path to saved DOCX.
    """
    validate_summons_data(json_data)
    project_dir = _project_dir()
    output_dir = os.path.join(project_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, output_filename)
    fill_template_from_data(template_path, json_data, output_path)
    return output_path


# --- Auto Template Builder (LLM-detect fields → «FIELD_NAME» placeholders) ---


def build_auto_template_from_sample(
    sample_file,
    llm_callable=None,
    output_filename: str = "auto_generated_template.docx",
) -> str:
    """
    Build an automatic template from an uploaded sample DOCX:
    1. Extract full document text (body, headers, footers).
    2. LLM identifies dynamic case-specific fields → JSON field_name -> exact string.
    3. Replace all occurrences of each exact string with «FIELD_NAME» (paragraph-level, run-safe).
    4. Replace in paragraphs, table cells, headers, footers.
    5. Validate: no original value may remain; else raise TemplateValidationError.
    6. Save as output_filename.

    sample_file: path (str/pathlib) or file-like with .read().
    llm_callable: (prompt: str) -> str. If None, uses _call_llm.
    Returns path to saved template DOCX.
    """
    if hasattr(sample_file, "read"):
        sample_file.seek(0)
        data = sample_file.read()
        sample_file.seek(0)
        tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
        tmp.write(data)
        tmp.flush()
        sample_path = tmp.name
        cleanup = True
    else:
        sample_path = sample_file
        cleanup = False
    try:
        project_dir = _project_dir()
        output_dir = os.path.join(project_dir, "output")
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, output_filename)
        llm = llm_callable if callable(llm_callable) else _call_llm
        builder = AutoTemplateBuilder(llm_callable=llm)
        result = builder.build(sample_path, output_path=output_path)
        return result["output_path"]
    finally:
        if cleanup and os.path.isfile(sample_path):
            try:
                os.unlink(sample_path)
            except OSError:
                pass
