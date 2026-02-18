"""
Step 5a: For each section, generate a formatting instruction (style, spacing, numbering, position)
from the sample section text and the template's style information.
These instructions are used to format each generated section one by one (step 5b in formatting).
"""
from docgen.llm_client import LLMClient
from docgen.prompts import build_section_formatting_instruction_prompt

llm = LLMClient()


def _template_content_to_string(template_content: list) -> str:
    """Turn template_content (list of {style, text}) into a string for the prompt."""
    if not template_content:
        return ""
    lines = []
    for item in template_content:
        style = item.get("style") or "Normal"
        text = (item.get("text") or "").strip()
        lines.append(f"[{style}]: {text}" if text else f"[{style}]:")
    return "\n".join(lines)


def generate_section_formatting_instructions(
    sections_list: list[dict],
    extracted_samples: list[str],
    template_content: list[dict],
    style_guide: str = "",
) -> list[str]:
    """
    For each section, generate a formatting instruction (how to apply template formatting to that section).
    Returns list of strings, one per section, in the same order as sections_list.
    template_content: from formatting's extract_styles(doc)["template_content"].
    style_guide: optional, from extract_styles(doc)["style_guide"].
    """
    template_content_str = _template_content_to_string(template_content)
    instructions = []
    for i, sec in enumerate(sections_list):
        sample_text = extracted_samples[i] if i < len(extracted_samples) else ""
        prompt = build_section_formatting_instruction_prompt(
            section_name=sec.get("name", ""),
            purpose=sec.get("purpose", ""),
            sample_section_text=sample_text,
            template_content_str=template_content_str,
            style_guide_str=style_guide,
        )
        raw = llm.generate(prompt, max_tokens=1024, temperature=0.1).strip()
        instructions.append(raw or "")
    return instructions
