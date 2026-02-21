"""
End-to-end pipeline: identify sections → extract text (chunked, no truncation) → prompts+fields → API → generate sections → assemble.
Full document content is used; extraction is chunked so responses stay within token limits.
Uses Pipeline class (OOP).
"""
from docgen.sectioner import Sectioner
from docgen.extractor import Extractor
from docgen.section_prompt_generator import SectionPromptGenerator
from docgen.field_fetcher import FieldFetcher
from docgen.question_generator import QuestionGenerator
from docgen.section_generator import SectionGenerator
from docgen.assembler import Assembler
from docgen.utils import fill_placeholders_from_context_with_llm


class Pipeline:
    """
    Runs the full document generation pipeline:
    1. Identify sections (name + purpose)
    2. Extract section text (chunked), then generate prompt + required_fields per section
    3. Collect unique required fields and fetch values via chat API
    4. Generate each section; optionally add case summary / extra context
    5. Assemble final draft in blueprint order with renumbering
    """

    def __init__(
        self,
        sectioner: Sectioner | None = None,
        extractor: Extractor | None = None,
        section_prompt_generator: SectionPromptGenerator | None = None,
        field_fetcher: FieldFetcher | None = None,
        question_generator: QuestionGenerator | None = None,
        section_generator: SectionGenerator | None = None,
        assembler: Assembler | None = None,
    ):
        self._sectioner = sectioner or Sectioner()
        self._extractor = extractor or Extractor()
        self._section_prompt_generator = section_prompt_generator or SectionPromptGenerator()
        self._field_fetcher = field_fetcher or FieldFetcher()
        self._question_generator = question_generator or QuestionGenerator()
        self._section_generator = section_generator or SectionGenerator()
        self._assembler = assembler or Assembler()

    def run(
        self,
        doc1: str,
        doc2: str,
        curl_str: str | None = None,
        extra_context: str | None = None,
        api_url: str | None = None,
    ) -> dict:
        """
        Returns dict with:
          blueprint, section_prompts, field_values, generated_sections, final_draft, etc.
        """
        blueprint = self._sectioner.divide_into_sections(doc1, doc2)
        sections_list = blueprint["sections"]

        extracted_samples = self._extractor.extract_sections_from_docs(doc1, doc2, sections_list)
        section_prompts_list = []
        for i, sec in enumerate(sections_list):
            sample_text = extracted_samples[i] if i < len(extracted_samples) else ""
            section_prompts_list.append(
                self._section_prompt_generator.generate_prompt_and_fields(
                    sec["name"], sec.get("purpose", ""), sample_text
                )
            )

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
                field_to_question = self._question_generator.generate_questions_for_fields(all_required)
                field_values = self._field_fetcher.fetch_all_fields_via_chat(
                    auth_str, all_required, field_to_question
                )

        if (extra_context or "").strip():
            field_values["case_summary_or_context"] = (extra_context or "").strip()

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
            text = self._section_generator.generate_section(
                prompt,
                section_field_values,
                sample_text=sample_text,
                section_name=name,
            )
            generated_sections[name] = text
            section_texts_ordered.append(text)

        final_draft = self._assembler.assemble(blueprint, section_texts_ordered)
        final_draft = fill_placeholders_from_context_with_llm(final_draft, field_values)

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


def run(
    doc1: str,
    doc2: str,
    curl_str: str | None = None,
    extra_context: str | None = None,
    api_url: str | None = None,
):
    """Backward-compatible: delegates to Pipeline().run."""
    return Pipeline().run(doc1, doc2, curl_str=curl_str, extra_context=extra_context, api_url=api_url)
