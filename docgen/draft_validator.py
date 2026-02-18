"""
Compare the final draft with two sample documents. Identify missing data (regenerate),
extra or different-tone content (remove/rewrite), and output a refined draft that
matches the samples as closely as possible in structure, tone, and completeness.
"""
from docgen.llm_client import LLMClient
from docgen.prompts import build_draft_validation_refinement_prompt

_llm = LLMClient()


def validate_and_refine_draft(
    final_draft: str,
    sample1: str,
    sample2: str,
    *,
    max_tokens: int = 8192,
    temperature: float = 0.05,
) -> str:
    """
    Compare final_draft to sample1 and sample2. Regenerate missing content (present
    in samples but not in draft), remove or rewrite extra headings and content with
    different tone, and return a refined draft aligned with the samples.

    Returns the refined draft text (same structure/tone as samples, no extra parts).
    """
    if not (final_draft or "").strip():
        return final_draft
    if not (sample1 or "").strip() and not (sample2 or "").strip():
        return final_draft

    sample1 = (sample1 or "").strip()
    sample2 = (sample2 or "").strip()
    prompt = build_draft_validation_refinement_prompt(final_draft, sample1, sample2)
    refined = _llm.generate(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return (refined or "").strip()
