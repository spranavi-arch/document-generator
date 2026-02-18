"""
For each section: generate (1) a prompt to create that section, (2) list of required fields.
Uses extracted sample text for each section (no full-doc pass).
"""
from docgen.llm_client import LLMClient
from docgen.prompts import build_section_prompt_and_fields_prompt
from docgen.utils import extract_json_from_llm

llm = LLMClient()


def generate_prompt_and_fields(
    section_name: str,
    purpose: str,
    sample_text: str,
) -> dict:
    """
    Returns { "prompt": str, "required_fields": list[str] }.
    sample_text: the extracted text for this section from the sample document(s).
    Formatting (style, spacing, numbering) is handled in Step 5, not here.
    """
    prompt = build_section_prompt_and_fields_prompt(
        section_name, purpose, sample_text or ""
    )
    response = llm.generate(
        prompt,
        json_mode=True,
        max_tokens=4096,
        temperature=0.15,
    )
    try:
        data = extract_json_from_llm(response)
    except ValueError:
        # Fallback: strong replication + new data only
        return {
            "prompt": f'Reproduce the sample section\'s exact language, wording, and tone for "{section_name}". Use the same sentence structures and phrasing. Only the factual data (names, dates, numbers) comes from the Field data below—use those values and do not invent. If a value is missing, output [field_name]. Output only the section body, no title.',
            "required_fields": [],
        }
    if not isinstance(data, dict):
        return {
            "prompt": f'Reproduce the sample\'s exact language and tone for "{section_name}". Only substitute the variable data with the Field data below. Output only the section body.',
            "required_fields": [],
        }

    section_prompt = data.get("prompt") or data.get("Section prompt") or ""
    required_fields = data.get("required_fields") or data.get("required_fields_list") or []
    if isinstance(required_fields, str):
        required_fields = [f.strip() for f in required_fields.split(",") if f.strip()]

    return {
        "prompt": section_prompt.strip() or f'Reproduce the sample\'s exact language and tone for "{section_name}". Data from Field data below only. Output only the section body.',
        "required_fields": list(required_fields),
    }
