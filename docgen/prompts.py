"""
Prompts for the DOCUMENT GENERATION pipeline only:
  (1) Dividing documents into sections
  (2) Extracting section text from a document
  (3) Generating per-section prompts + required fields + section structure description
  (4) Draft validation (compare draft to samples)

Formatting (applying DOCX styles to the final draft) has its own prompts and code
in the formatting/ module — do not mix formatting logic or prompts here.
"""

# Generalized document rules for all document types (Motion, Summons & Complaint, Notice of Claim, Petition, Affidavit, etc.).
DOCUMENT_RULES = """
DOCUMENT RULES (follow for every section — applies to all document types):

1. Analyze the reference: document type (e.g. Motion, Summons & Complaint, Notice of Claim, Petition, Affidavit), section headings, section order, writing style and tone, level of legal detail.
2. Reproduce the SAME structure and section sequence as the reference.
3. Use each section only once. Do NOT repeat or merge multiple versions.
4. Maintain clear separation between major parts (e.g. summons vs complaint, motion vs affidavit, notice vs certification).
5. Use formal court-appropriate legal language.
6. Maintain internal consistency: party names, case number, court name, dates, addresses, attorney information (same spelling and form throughout).
7. Where case facts are missing, use clear placeholders: [Date], [Index No.], [Judge Name], [Attorney Name], etc.
8. Expand legal reasoning and factual allegations to match the depth of the reference document.
9. Ensure logical progression: Jurisdiction → Facts → Legal Basis → Relief → Signatures → Verifications.
10. Do NOT copy text verbatim from the reference. Adapt structure and style using the provided case data.
11. Output ONLY one clean, finalized document. Do not include analysis, explanations, or comments.
"""


# this is legacy prompt
def build_extract_section_prompt(doc: str, section_name: str) -> str:
    """Prompt to extract one section's full, verbatim text from a legal document by section name."""
    return f"""You are extracting a single section from a legal document. Your task is to return ONLY the full, verbatim text of the section that corresponds to: "{section_name}".

Section identification:
- The section may appear under a heading or title that matches or closely resembles "{section_name}" (e.g. "Caption", "Summons", "Signature", "Verification", "Allegations", "Prayer for Relief").
- If there is no exact heading, identify the logical part of the document that serves this purpose (e.g. the caption is the court/parties/case-number block at the top; the signature block is the attorney signature and date at the end).
- Include the section heading/title if it appears in the document, then the entire body of that section.
- Stop at the start of the next distinct section (next heading or next logical part). Do not include text from other sections. Output one contiguous block only — no gaps, no merged sections.

Critical rules:
- Output ONLY the document text that belongs to this section — nothing else. Do NOT add the section name, "Section: ...", "Purpose: ...", or any label or meta-description. Only the exact text as it appears in the document.
- Copy text EXACTLY: same wording, line breaks, numbering, spacing. Do not paraphrase or summarize.
- Include everything that belongs to this section (headings that appear in the doc, caption lines, paragraphs, signature blocks, etc.). If the document has a heading like "SUMMONS", that stays; do not add extra labels.
- If no part of the document corresponds to "{section_name}", return empty content.
- Return a JSON object with exactly one key: "content". The value is the extracted section text. Use \\n for newlines. No other keys, no commentary.

Document:
---
{doc}
---
"""


# this is prompt used to get the document text for each section in one call which we are not using currently
def build_split_document_into_sections_prompt(doc: str, sections: list[dict]) -> str:
    """
    Prompt to split a single document into exactly the given ordered sections.
    sections: list of {"name": str, "purpose": str} in reading order.
    Returns JSON with "sections": [ "text1", "text2", ... ] — document text only, no labels.
    """
    n = len(sections)
    section_list_text = "\n".join(
        f"  {i + 1}. Name: \"{s.get('name', '')}\" — Purpose: {s.get('purpose', '') or '(see name)'}"
        for i, s in enumerate(sections)
    )
    return f"""You are splitting a legal document into exactly {n} sections. Use the section names and purposes below ONLY to identify which part of the document belongs where. Do NOT write section names or purposes into your output.

Sections (strict reading order, top to bottom):
{section_list_text}

CRITICAL RULES:
1. OUTPUT DOCUMENT TEXT ONLY: Each string in "sections" must be the exact text that appears in the document — nothing else. Do NOT add labels, titles like "Section: Caption", "Purpose: ...", or the section name. Do NOT write "This section contains..." or any meta-description. Only the actual document content that belongs to that section, copied verbatim.
2. LOGICAL MATCH: Assign each part of the document to the section it logically belongs to according to name and purpose. Caption/header → first section; summons notice → Summons section; allegations → Allegations; signature block → Signature; etc. The flow of the document must be continuous: section 1, then section 2, then section 3, in order, with no gaps.
3. FULL COVERAGE: The extracted sections must cover the WHOLE document. From the first character to the last, every line and paragraph must appear in exactly one section. Do not skip or omit any part. The concatenation of all section texts (in order) should equal the full document.
4. NO OVERLAP: No text may appear in more than one section. Section 1 ends exactly where section 2 begins; section 2 ends where section 3 begins.
5. VERBATIM: Copy text exactly as in the document — same wording, line breaks, numbering, spacing. If the document has a heading (e.g. "SUMMONS"), that heading is part of the document and stays in the section it introduces.

Method: Read the document from top to bottom. For each section in order, find the contiguous span that matches that section's name and purpose. Output only that span of document text. Ensure the whole document is covered with no gaps and no duplication.

Return ONLY valid JSON (use \\n for newlines inside strings):
{{
  "sections": [
    "document text for section 1 only — no labels",
    "document text for section 2 only — no labels",
    ...
  ]
}}

Exactly {n} strings. If a section has no content, use "". No other keys, no commentary.

Document:
---
{doc}
---
"""


# we are using this currently
# Chunk size for extraction (sections per LLM call) to stay under output token limit and avoid truncation.
EXTRACTION_CHUNK_SIZE = 4


def build_split_document_into_sections_chunk_prompt(
    doc: str, sections: list[dict], start_idx: int, end_idx: int
) -> str:
    """
    Chunked split: output text only for sections[start_idx:end_idx].
    Full document section order is provided so boundaries can be determined accurately.
    """
    chunk_sections = sections[start_idx:end_idx]
    n_total = len(sections)
    k = len(chunk_sections)
    # Full ordered list of all section names so model knows what comes before/after
    full_order = "\n".join(
        f"  {i + 1}. {s.get('name', '')} — {s.get('purpose', '') or '(see name)'}"
        for i, s in enumerate(sections)
    )
    chunk_list_text = "\n".join(
        f"  Section {start_idx + i + 1}. Name: \"{s.get('name', '')}\" — Purpose: {s.get('purpose', '') or '(see name)'}"
        for i, s in enumerate(chunk_sections)
    )
    prev_name = sections[start_idx - 1].get("name", "previous") if start_idx > 0 else "(start of document)"
    next_name = sections[end_idx].get("name", "next") if end_idx < n_total else "(end of document)"
    return f"""You are extracting sections {start_idx + 1} through {start_idx + k} from a legal document. Accuracy is critical: each section must contain ONLY the text that belongs to that section according to its name and purpose.

FULL DOCUMENT SECTION ORDER (all {n_total} sections — use this to set exact boundaries):
{full_order}

SECTIONS YOU MUST EXTRACT (sections {start_idx + 1}–{start_idx + k}):
{chunk_list_text}

BOUNDARY RULES:
- Sections are CONTIGUOUS: the last character of one section is immediately followed by the first character of the next. There are no gaps. Use the full order above to find where section {start_idx} ends and section {start_idx + 1} begins, and where section {start_idx + k} ends and section {start_idx + k + 1} begins.
- Section {start_idx + 1} contains ONLY content that belongs to "{chunk_sections[0].get('name', '')}" (and its purpose). It starts at the first character of that content. It does NOT include anything from "{prev_name}".
- Section {start_idx + k} contains ONLY content that belongs to "{chunk_sections[-1].get('name', '')}". It ends at the last character of that content. It does NOT include anything from "{next_name}".
- If a heading appears in the document (e.g. "SUMMONS", "ALLEGATIONS"), that heading belongs to the section it introduces and stays in that section's text.

CRITICAL:
1. OUTPUT DOCUMENT TEXT ONLY: Each string = exact verbatim text from the document for that section. No labels, no "Section: ...", no meta-description. Only the actual document content.
2. ACCURATE TO SECTION: Assign each line to the section that matches its name and purpose. Do not put content that belongs to another section (e.g. do not put summons text in Caption, or signature in Verification).
3. FULL COVERAGE: Every character of the document that falls in sections {start_idx + 1}–{start_idx + k} must appear in your output. No omissions.
4. NO OVERLAP: No character may appear in more than one section. Verbatim copy — same wording, line breaks, numbering, spacing.

Return ONLY valid JSON (use \\n for newlines inside strings):
{{
  "sections": [
    "document text for section {start_idx + 1} only",
    "document text for section {start_idx + 2} only",
    ...
  ]
}}

Exactly {k} strings. No other keys, no commentary.

Document (full document — determine exact boundaries for sections {start_idx + 1} to {start_idx + k}):
---
{doc}
---
"""


# both identify sections and extract the text from both docs. We are not using it currently
def build_sectioning_and_extraction_prompt(doc1: str, doc2: str) -> str:
    """
    Single-step prompt: identify sections (name, purpose) AND extract the exact text for each section from both documents. No separate extraction step — avoids confusion and repetition.
    """
    return f"""You are an expert document analyst. Your task is to:
1. IDENTIFY logical sections in BOTH documents (same section list for both). Each section has a name and purpose.
2. EXTRACT the exact verbatim text for each section from Document 1 and from Document 2.
Do this in ONE pass so that section boundaries are consistent and there is no overlap or repetition.

RULES:
- Sections are in strict reading order (top to bottom). Examples: Caption, Summons, Parties, Allegations, Prayer for Relief, Signature, Verification, Certification, etc. Include all distinct parts (at least 11 sections when the documents support it).
- NO OVERLAP: In Document 1, every line appears in exactly one section. In Document 2, every line appears in exactly one section. Do not put the same text in two sections.
- NO GAPS: From the first line to the last line of each document, every part is assigned to a section. Do not skip content.
- MATCH BY LOGIC: The same section (e.g. "Caption") gets the caption text from Doc 1 and the caption text from Doc 2. Use the section name and purpose to decide what belongs where.
- VERBATIM: Copy text exactly as it appears — same wording, line breaks, numbering, spacing. No paraphrasing.
- For each section, output: "name", "purpose", "text_from_doc1", "text_from_doc2". Use \\n for newlines inside the text strings. If a section has no content in one document, use "" for that text.

Return ONLY valid JSON (no other text):
{{
  "sections": [
    {{
      "name": "Exact section name or heading",
      "purpose": "Brief purpose in one line",
      "text_from_doc1": "Full verbatim text of this section from Document 1",
      "text_from_doc2": "Full verbatim text of this section from Document 2"
    }},
    ...
  ]
}}

You must have at least 11 items in "sections". Each item must have "name", "purpose", "text_from_doc1", and "text_from_doc2".

Document 1:
---
{doc1}
---

Document 2:
---
{doc2}
---
"""


# prompt used to get sections
def build_sectioning_prompt(doc1: str, doc2: str) -> str:
    """Prompt so the LLM divides both documents into logical sections. Sections must be clearly identified so extraction can assign every line to exactly one section."""
    return f"""You are an expert document analyst. Your task is to identify CLEARLY DEFINED sections so that every part of the document can be accurately extracted and assigned to exactly one section.

Step 1 — Analyze the reference documents:
- Document type (e.g. Motion, Summons & Complaint, Notice of Claim, Petition, Affidavit)
- Every heading, label, or distinct block that appears in the documents (use exact wording when present, e.g. "SUMMONS", "FIRST CAUSE OF ACTION", "VERIFICATION")
- Strict reading order from top to bottom
- Writing style and level of legal detail

Step 2 — Output the section list. CRITICAL: sections must be clearly identified and accurate for extraction.

Requirements:
- CLEAR IDENTIFICATION: Use the exact section heading as it appears in the document when there is one (e.g. "HEADING" ,"SUMMONS", "COMPLAINT", "PRAYER FOR RELIEF"). When there is no heading, use a precise standard name (e.g. "Caption", "Signature Block", "Verification").
- SPECIFIC PURPOSE: For each section, write a "purpose" that describes exactly what content belongs in that section so extraction can assign every line correctly. Examples: "Court name, case index number, party names, and case title at top of first page"; "Summons notice text and 'TO THE ABOVE NAMED DEFENDANT' through end of summons"; "Numbered allegations or causes of action only".
- MORE SECTIONS IS BETTER: Prefer more, clearly separated sections over fewer merged ones. Split distinct parts (e.g. "Caption" and "Parties/Case Title", "Signature" and "Date", "Verification" and "Certification", each "Cause of Action" if numbered separately). At least 11 sections; 12–18 or more is preferred when the documents have many distinct parts.
- ONE-TO-ONE: Every line of both documents must belong to exactly one section. No gaps, no overlap. Sections are in strict reading order.
- FULL COVERAGE: Include all distinct parts: caption, court/venue, parties, summons text, complaint/petition body, each cause of action or allegation block if separate, prayer for relief, signature block, date, verification, certification, filing instructions, proof of service, etc.

Output format — return valid JSON only:
{{
  "sections": [
    {{
      "name": "Exact section name or heading as in document",
      "purpose": "Specific description of what content belongs in this section only"
    }},
    ...
  ]
}}

Document 1:
---
{doc1}
---

Document 2:
---
{doc2}
---
"""


# prompt to build prompt from full docs which we are not using now
def build_section_prompt_and_fields_prompt_from_full_docs(
    section_name: str, purpose: str, doc1: str, doc2: str
) -> str:
    """Use both full sample documents for accuracy; no extraction step."""
    return f"""You are building a template for the section "{section_name}" of a legal document. Purpose of this section: {purpose or "See documents for structure."}

Below are the TWO FULL sample documents. Identify the part in BOTH documents that corresponds to the section "{section_name}". Use the full context for accuracy.

Document 1:
---
{doc1 or "(empty)"}
---

Document 2:
---
{doc2 or "(empty)"}
---

Perform two tasks and return a single JSON object.

Task 1 — Section generation prompt:
Write a detailed prompt that will be given to another LLM. The prompt must state the following clearly and firmly:
1. FORMAT AND STRUCTURE FROM THE SAMPLE: The model must reproduce the sample section's **format, structure, layout, and tone** (headings, caption block, numbering style, "TO THE ABOVE NAMED DEFENDANT:", signature blocks, spacing). Variable facts (names, dates, court, index no., addresses, etc.) come from Field data only.
2. CASE TYPE MATTERS: The sample may be from a different type of case (e.g. premises liability, motor vehicle, medical malpractice). The model will be told the **new case type**. For **boilerplate** sections (caption, summons notice, signature), use the same wording and substitute data. For **cause-of-action-specific** sections (allegations, legal claims, negligence theories), use legal language **appropriate to the new case type**—do not copy the sample's substantive legal wording if it does not fit (e.g. do not use "premises" or "motor vehicle" language in a medical malpractice case).
3. DATA FROM FIELD DATA ONLY: Every fact (party names, dates, amounts, case numbers, court name, etc.) must come from Field data. Do not invent. If a value is missing, output [field_name].
4. OUTPUT: Only the actual document text as filed. No summaries, no "Here are the addresses...", no "The primary plaintiff is...". Document text only.
5. LEGAL PROSE ONLY: The body of the section must be written as **continuous legal prose**—full sentences and paragraphs in formal court-appropriate language. Do NOT output bullet points, numbered sub-headers, or markdown-style lists.
6. DOCUMENT RULES (all types): The prompt must require: same structure and section sequence as reference; each section once, no repeat or merge; clear separation between major parts (summons vs complaint, motion vs affidavit, etc.); formal court language; internal consistency (party names, case number, court, dates, addresses, attorney info); placeholders like [Date], [Index No.], [Judge Name], [Attorney Name] when data is missing; expand legal reasoning and allegations to match reference depth; logical progression (Jurisdiction → Facts → Legal Basis → Relief → Signatures → Verifications); do NOT copy text verbatim—adapt structure and style using case data; output only one clean finalized document with no analysis or explanations.

Task 2 — Required fields:
List ONLY the fields that are actually used or referenced in THIS section (snake_case). Do not list fields that do not appear in this section. For example, a caption section might need only plaintiff_name, defendant_name, case_number, court_name; a signature block might need attorney_name, date_of_filing. Extract from the documents which variables appear in this section—one section may need 2–4 fields, another more. One field per distinct value. These will be fetched from an API.

Return ONLY this JSON (escape quotes as \\\" and newlines as \\n in the "prompt" value):
{{
  "prompt": "Full prompt text for generating this section.",
  "required_fields": ["field1", "field2", "field3"]
}}
"""


# -----------------------------------------------------------------------------
# Section template: generation prompt + required fields (content only; formatting is Step 5)
# -----------------------------------------------------------------------------

def build_section_prompt_and_fields_prompt(section_name: str, purpose: str, sample_text: str) -> str:
    """Prompt to generate (1) section-generation prompt and (2) required fields. Formatting (style, spacing, etc.) is handled in Step 5."""
    return f"""You are building a template for the section "{section_name}" of a legal document. Purpose: {purpose or "See sample for structure."}

The sample below is the EXACT content that was extracted for this section. You will produce: a generation prompt and required fields. (Formatting—styles, spacing, numbering—will be applied in a later step.)

Sample content for this section (only variable data will change in generation):
---
{sample_text or "(No sample; use standard legal format.)"}
---

Perform two tasks and return a single JSON object.

Task 1 — Section generation prompt:
Write a detailed prompt for another LLM that will generate ONLY this section — no spillover into the previous or next section. The prompt must be SPECIFIC and ACCURATE so that the output matches the sample in content and scope.

The generated prompt MUST:
1. DEFINE SECTION SCOPE EXPLICITLY: State "This section is [name]. It contains exactly: [list what is in the sample]. Do NOT include [content that belongs to the previous or next section]."
2. DATA FROM FIELD DATA ONLY: All variable facts (names, dates, court, index no., addresses, attorney) come from Field data. Missing → [field_name]. No invented facts.
3. OUTPUT: Only the actual document text for this section. No summaries, no meta-commentary. Document text only.
4. LEGAL PROSE: Continuous formal legal prose; no bullets or markdown. For allegations/claims, adapt legal language to the new case type (do not copy sample's cause-of-action wording if it does not fit).
5. ONE SECTION ONLY: The prompt must instruct the model to output ONLY the content for this section and to stop before the next section's heading or content.

Task 2 — Required fields:
List ONLY the fields that appear or are referenced in THIS section (snake_case). One field per distinct value. Extract from the sample. These will be fetched from an API.

Return ONLY this JSON (escape quotes as \\\" and newlines as \\n in string values):
{{
  "prompt": "Full prompt text for generating this section accurately.",
  "required_fields": ["field1", "field2", "field3"]
}}
"""


# -----------------------------------------------------------------------------
# Step 5: Per-section formatting instruction (how to apply template formatting to each section)
# Used when building the final formatted document section-by-section.
# -----------------------------------------------------------------------------

def build_section_formatting_instruction_prompt(
    section_name: str,
    purpose: str,
    sample_section_text: str,
    template_content_str: str,
    style_guide_str: str = "",
) -> str:
    """Prompt to generate a formatting instruction for this section: text style, color, spacing, numbering, position.
    The instruction will be used to format the generated section text so it matches the sample's appearance."""
    style_block = f"\nTemplate paragraph styles (each line is [StyleName]: paragraph text):\n{template_content_str}\n" if template_content_str else ""
    if style_guide_str:
        style_block += f"\nStyle guide (font, alignment, spacing per style):\n{style_guide_str[:3000]}\n"
    return f"""You are analyzing how ONE section of a legal document is formatted in the sample, so the same formatting can be applied to new text for that section.

Section: "{section_name}"
Purpose: {purpose or "(see sample)"}

Sample content for this section (exactly as it appears in the template document):
---
{sample_section_text or "(No sample text)"}
---
{style_block}

Task: Write a FORMATTING INSTRUCTION for this section. Describe exactly how this section is formatted so that another process can apply the same formatting to new text. Include:
1. Which Word/style name to use for each part (e.g. first line = Heading 1, body = Normal, list = List Paragraph).
2. Spacing: space before/after paragraphs, line spacing, indents (first-line, left).
3. Numbering: if any paragraph is numbered (1. 2. 3. or (a) (b)), which style and how.
4. Alignment: left, center, right, justify for each part.
5. Position: anything that starts on a new page, or has a line/underline below it.
6. Separator lines: dashes, underscores, lines ending in X — use block_type "line" or "signature_line" where appropriate.
7. Font/emphasis: bold, italic, font size if different from Normal.

Be concrete and ordered (first block → style X, second block → style Y, etc.). Use the exact style names from the template. Output plain text only; this instruction will be passed to a formatter that applies it to new section text.

Return ONLY the formatting instruction (no JSON, no "Instruction:" label). One or more paragraphs."""


# -----------------------------------------------------------------------------
# Draft validation: compare final draft to samples, fill missing, remove extra
# -----------------------------------------------------------------------------

DRAFT_VALIDATION_REFINEMENT_INSTRUCTIONS = """
You are validating and refining a generated legal document draft against two ORIGINAL SAMPLE documents.

Your task:
1. COMPARE the final draft to SAMPLE 1 and SAMPLE 2.
2. Identify:
   - MISSING: Content that appears in the samples (sections, headings, paragraphs, standard clauses, signature blocks, captions) but is absent or underdeveloped in the draft. This includes structural elements (e.g. a required heading or numbered list) and tone/depth that the samples have but the draft lacks.
   - EXTRA or WRONG TONE: Content in the draft that (a) does not appear in the samples (extra headings, invented sections, redundant blocks), or (b) has a different tone (conversational, instructional, markdown, informal) instead of the formal legal style of the samples.
3. REFINE the draft so that:
   - All missing elements are added: generate any missing sections/headings/paragraphs in the same style, structure, and tone as the samples. Use the same level of detail and formatting (numbering, spacing, captions) as in the samples. Do not invent facts; use placeholders like [field_name] where data is unknown.
   - Extra or off-tone content is removed or rewritten: delete headings/sections that do not exist in the samples; rewrite any text that sounds different from the samples so it matches their formal legal tone and structure.
4. OUTPUT only the refined document text. No commentary, no "here is the refined draft", no bullet lists or analysis. Output the complete, filing-ready document that is as close as possible to the original samples in structure, tone, and completeness.
"""


def build_draft_validation_refinement_prompt(final_draft: str, sample1: str, sample2: str) -> str:
    """Build prompt for comparing draft to two samples and returning refined draft."""
    return f"""
{DRAFT_VALIDATION_REFINEMENT_INSTRUCTIONS}

---
SAMPLE DOCUMENT 1:
---
{sample1}

---
SAMPLE DOCUMENT 2:
---
{sample2}

---
CURRENT FINAL DRAFT TO VALIDATE AND REFINE:
---
{final_draft}

---
Output ONLY the refined document text (no explanations, no JSON, no markdown). The refined document must align with the samples in structure, headings, tone, and completeness; missing parts filled, extra or off-tone parts removed or corrected.
"""
