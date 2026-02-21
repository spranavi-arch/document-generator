"""
Step 5a: For each section, generate a formatting instruction (style, spacing, numbering, position)
from the sample section text and the template's style information.
These instructions are used to format each generated section one by one (step 5b in formatting).
Uses SectionFormattingPromptGenerator class (OOP).
"""
from docgen.llm_client import LLMClient
from docgen.prompts import PromptsBuilder


class SectionFormattingPromptGenerator:
    """
    Generates per-section formatting instructions so the same formatting
    can be applied to new section text (style, spacing, numbering, position).
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or LLMClient()

    @staticmethod
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
        self,
        sections_list: list[dict],
        extracted_samples: list[str],
        template_content: list[dict],
        style_guide: str = "",
    ) -> list[str]:
        """
        For each section, generate a formatting instruction.
        Returns list of strings, one per section, in the same order as sections_list.
        """
        template_content_str = self._template_content_to_string(template_content)
        instructions = []
        for i, sec in enumerate(sections_list):
            sample_text = extracted_samples[i] if i < len(extracted_samples) else ""
            prompt = PromptsBuilder.build_section_formatting_instruction_prompt(
                section_name=sec.get("name", ""),
                purpose=sec.get("purpose", ""),
                sample_section_text=sample_text,
                template_content_str=template_content_str,
                style_guide_str=style_guide,
            )
            raw = self._llm.generate(prompt, max_tokens=1024, temperature=0.1).strip()
            instructions.append(raw or "")
        return instructions


def generate_section_formatting_instructions(
    sections_list: list[dict],
    extracted_samples: list[str],
    template_content: list[dict],
    style_guide: str = "",
) -> list[str]:
    """Backward-compatible: delegates to SectionFormattingPromptGenerator().generate_section_formatting_instructions."""
    return SectionFormattingPromptGenerator().generate_section_formatting_instructions(
        sections_list, extracted_samples, template_content, style_guide
    )
