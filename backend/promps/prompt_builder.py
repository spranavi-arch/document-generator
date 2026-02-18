class PromptBuilder:

    def build(self, name, purpose, examples):
        """Build a prompt so the LLM knows: what to take from the Case Summary, and which format to use (from sample)."""
        return f"""You are writing the "{name}" section of a legal document.

Purpose of this section: {purpose or "See sample for structure."}

Use the Case Data/Summary provided below to get the facts and information for this section. Write only what belongs in this section. Match the format, style, and structure of the sample(s) below so the new section looks like the examples.

Sample text for this section (format to follow):
---
{examples or "(No sample provided; use standard legal format for this section.)"}
---

Rules:
- Get all relevant information from the Case Data for this section only.
- Follow the exact format and style of the sample (headings, spacing, wording patterns).
- Do not invent facts; use only what is in the Case Data.
- Output only the section text, no meta-commentary.
- Do NOT include the section name or title in your output. Write only the body content so this section reads as a direct continuation of the document, not as a new headed section."""
