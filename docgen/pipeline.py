"""
End-to-end pipeline: identify sections → extract text (chunked, no truncation) → prompts+fields → API → generate sections → assemble.
Full document content is used; extraction is chunked so responses stay within token limits.
"""
from docgen.sectioner import divide_into_sections
from docgen.extractor import extract_sections_from_docs
from docgen.section_prompt_generator import generate_prompt_and_fields
from docgen.field_fetcher import fetch_all_fields_via_chat
from docgen.question_generator import generate_questions_for_fields
from docgen.section_generator import generate_section
from docgen.assembler import assemble


def run(
    doc1: str,
    doc2: str,
    curl_str: str | None = None,
    extra_context: str | None = None,
    api_url: str | None = None,
):
    """
    Returns dict with:
      blueprint, section_prompts, field_values, generated_sections, final_draft
    """
    # 1. Identify sections (name + purpose only)
    blueprint = divide_into_sections(doc1, doc2)
    sections_list = blueprint["sections"]

    # 2. Extract section text in chunks (full document, no truncation), then generate prompt + required_fields
    extracted_samples = extract_sections_from_docs(doc1, doc2, sections_list)
    section_prompts_list = []
    for i, sec in enumerate(sections_list):
        sample_text = extracted_samples[i] if i < len(extracted_samples) else ""
        section_prompts_list.append(
            generate_prompt_and_fields(sec["name"], sec.get("purpose", ""), sample_text)
        )

    # 3. Collect all unique required fields and fetch each value via chat API
    all_required = []
    seen = set()
    for info in section_prompts_list:
        for f in info.get("required_fields", []):
            if f and f not in seen:
                seen.add(f)
                all_required.append(f)

    field_values = {}
    auth_str = (curl_str or "").strip()
    if auth_str:
        if all_required:
            field_to_question = generate_questions_for_fields(all_required)
            field_values = fetch_all_fields_via_chat(
                auth_str, all_required, field_to_question
            )

    if (extra_context or "").strip():
        field_values["case_summary_or_context"] = (extra_context or "").strip()

    # 4. Generate each section using prompt, structure description, and extracted sample text
    generated_sections = {}
    section_texts_ordered = []
    for i, sec in enumerate(sections_list):
        name = sec["name"]
        info = section_prompts_list[i]
        prompt = info.get("prompt", "")
        required_fields = info.get("required_fields", [])
        section_field_values = {f: field_values.get(f, "") for f in required_fields}
        if extra_context:
            section_field_values["case_summary_or_context"] = (extra_context or "").strip()
        sample_text = extracted_samples[i] if i < len(extracted_samples) else ""
        text = generate_section(
            prompt,
            section_field_values,
            sample_text=sample_text,
            section_name=name,
        )
        generated_sections[name] = text
        section_texts_ordered.append(text)

    # 5. Assemble final draft (ordered list avoids repeated content when section names duplicate)
    final_draft = assemble(blueprint, section_texts_ordered)

    return {
        "blueprint": blueprint,
        "sections_list": sections_list,
        "extracted_samples": extracted_samples,
        "section_prompts": section_prompts_list,
        "field_values": field_values,
        "generated_sections": generated_sections,
        "section_texts_ordered": section_texts_ordered,
        "final_draft": final_draft,
    }
