"""
Generate one section using the section prompt, extracted sample text for this section, and field values (from API).
Uses pre-extracted section sample text (no full-doc pass).
"""
from docgen.llm_client import LLMClient
from docgen.prompts import DOCUMENT_RULES

llm = LLMClient()


def format_field_data(field_values: dict) -> str:
    """Format key-value pairs for appending to the prompt."""
    if not field_values:
        return "No field data provided."
    lines = [f"- **{k}**: {v}" for k, v in field_values.items()]
    return "\n".join(lines)


def generate_section(
    section_prompt: str,
    field_values: dict,
    sample_text: str | None = None,
    section_name: str | None = None,
) -> str:
    """
    Generate section body (content only) from the section prompt and extracted sample text.
    Formatting (style, spacing, numbering, position) is applied later in Step 5 (per-section formatting prompts).
    """
    field_block = format_field_data(field_values)
    sample_block = ""
    if (sample_text or "").strip():
        sample_block = f"""
---
SAMPLE FOR THIS SECTION (replicate format, structure, and tone — only data will change):
---
{(sample_text or "").strip()}
---
"""

    case_type_instruction = """
DOCUMENT TYPE: The sample may be a Summons & Complaint, a Motion (notice/affidavit/memorandum/order), a Notice of Claim, or another type. Use the sample for FORMAT and STRUCTURE only (layout, numbering, headings, signature/verification blocks).
- If the section is complaint-like (allegations, causes of action): use legal language appropriate to the NEW case type (e.g. premises, motor vehicle, medical malpractice); do not copy cause-of-action wording if it does not fit.
- If the section is motion-like (notice of motion, affidavit, memorandum, order): preserve notice/affidavit/memorandum style; substitute facts and relief from Field data.
- If the section is notice-of-claim-like (claimant, public corporation, nature of claim, time/place, damages): preserve statutory and factual block structure; substitute claimant/case data from Field data.
Do not force complaint structure (e.g. numbered allegations) onto a motion or notice of claim section.
"""

    section_scope = f'Output ONLY the content that belongs to this section ("{section_name or "this section"}"). Do not include the next section\'s heading or body.' if section_name else "Output ONLY the content for this section. Do not include the next section's heading or body."

    full_prompt = f"""You must output the document text for this section exactly as it would appear in the filed document. Do NOT write summaries, bullet lists, or meta-descriptions. Output ONLY the actual legal document text for THIS section.

{section_prompt}
{sample_block}
{case_type_instruction}
---
Field data (new case — substitute these values; do not invent):
{field_block}
---

CASE SUMMARY: If Field data above includes "case_summary" or "case_summary_or_context", use that data whenever this section needs contextual details, background facts, or narrative (e.g. allegations, statement of facts, notice text) that are not fully covered by the other fields. Use the case summary to inform what you write for this particular section; do not paste it verbatim unless it fits the section's purpose.

CRITICAL — follow exactly:
1. OUTPUT THE DOCUMENT TEXT ONLY for this section. Same layout and structure as the sample (court header, caption, body, date lines, signature blocks — whatever the sample for this section contains). No summaries or explanations. Only the real document text.
2. FORMAT & STRUCTURE: Replicate the sample's format, structure, spacing, and numbering for this section. Use Field data above for all variable facts (names, dates, court, index no., addresses, attorney info). If a value is missing, use placeholders: [Date], [Index No.], [Judge Name], [Attorney Name], etc.
3. DO NOT COPY VERBATIM: Adapt structure and style from the sample using the provided case data. Use formal court-appropriate legal language. Expand legal reasoning and factual allegations to match the depth of the sample.
4. CONSISTENCY: Use the same party names, case number, court name, dates, addresses, and attorney information throughout (same spelling and form).
5. MAXIMUM INFORMATION: Use all information in the Field data above, including the case summary when this section needs it. Do not omit facts, dates, or figures that appear in the Field data.
6. ONE SECTION ONLY: {section_scope} Do not merge multiple sections. No analysis, explanations, or comments.
{DOCUMENT_RULES}
"""
    return llm.generate(full_prompt, max_tokens=4096, temperature=0.05).strip()
