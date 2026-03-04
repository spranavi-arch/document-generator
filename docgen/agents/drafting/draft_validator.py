"""
Compare the final draft with two sample documents. Identify missing data (regenerate),
extra or different-tone content (remove/rewrite), and output a refined draft that
matches the samples as closely as possible in structure, tone, and completeness.
Uses DraftValidator class (OOP).
"""
from docgen.llm_client import LLMClient
from docgen.prompts import PromptsBuilder


class DraftValidator:
    """
    Validates and refines a generated draft against two sample documents:
    adds missing content, removes/rewrites extra or off-tone content.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self._llm = llm_client or LLMClient()

    def validate_and_refine_draft(
        self,
        final_draft: str,
        sample1: str,
        sample2: str,
        *,
        max_tokens: int = 8192,
        temperature: float = 0.05,
    ) -> str:
        """
        Compare final_draft to sample1 and sample2. Regenerate missing content,
        remove or rewrite extra/off-tone content, return refined draft.
        """
        if not (final_draft or "").strip():
            return final_draft
        if not (sample1 or "").strip() and not (sample2 or "").strip():
            return final_draft

        sample1 = (sample1 or "").strip()
        sample2 = (sample2 or "").strip()
        prompt = PromptsBuilder.build_draft_validation_refinement_prompt(final_draft, sample1, sample2)
        refined = self._llm.generate(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (refined or "").strip()


def validate_and_refine_draft(
    final_draft: str,
    sample1: str,
    sample2: str,
    *,
    max_tokens: int = 8192,
    temperature: float = 0.05,
) -> str:
    """Backward-compatible: delegates to DraftValidator().validate_and_refine_draft."""
    return DraftValidator().validate_and_refine_draft(
        final_draft, sample1, sample2, max_tokens=max_tokens, temperature=temperature
    )
