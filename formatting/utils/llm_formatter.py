"""Use an LLM to split and label text into styled blocks (block_type + text)."""

import base64
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor

from utils.style_extractor import build_section_formatting_prompts

# Phrases sometimes emitted by the model instead of/in addition to JSON; strip before parsing.
_LLM_REFUSAL_PATTERN = re.compile(
    r"\s*I'm sorry, but I can't assist with that\.?\s*",
    re.IGNORECASE,
)

# Explicit page break in raw text: a line of six or more backticks. Post-processing expands these into page_break blocks.
PAGE_BREAK_MARKER_REGEX = re.compile(r"\n\s*`{6,}\s*\n", re.MULTILINE)


def _strip_page_break_marker_in_text(text: str) -> str:
    """Remove the page-break marker line (6+ backticks) from text. Used for slot-fill so the marker does not appear in output."""
    if not text:
        return text
    return PAGE_BREAK_MARKER_REGEX.sub("\n", text).strip()


def _split_text_into_chunks(text: str, n: int) -> list[str]:
    """Split text into n roughly equal chunks by character count. Used for one-image-per-minute mode."""
    if not text or n <= 1:
        return [text] if text else []
    size = len(text)
    chunk_size = (size + n - 1) // n
    return [text[i * chunk_size : min((i + 1) * chunk_size, size)] for i in range(n)]


def _expand_page_break_markers(blocks: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Expand any block whose text contains the page-break marker into multiple blocks with page_break inserted.
    For free-form output only. Ensures explicit page breaks in raw text become doc.add_page_break() in the formatter."""
    if not blocks:
        return blocks
    out = []
    for block_type, text in blocks:
        if not text or not PAGE_BREAK_MARKER_REGEX.search(text):
            out.append((block_type, text))
            continue
        parts = PAGE_BREAK_MARKER_REGEX.split(text)
        for i, part in enumerate(parts):
            stripped = part.strip()
            if stripped:
                out.append((block_type, stripped))
            if i < len(parts) - 1:
                out.append(("page_break", ""))
    return out


def _strip_llm_refusal_artifact(raw: str) -> str:
    """Remove common refusal phrase that would break JSON (e.g. mid-response)."""
    return _LLM_REFUSAL_PATTERN.sub("\n", raw)


def _sanitize_json_control_chars(raw: str) -> str:
    """Replace unescaped control characters inside JSON string values so json.loads succeeds."""
    # Match double-quoted string contents (handles \" inside)
    result = []
    i = 0
    while i < len(raw):
        if raw[i] == '"' and (i == 0 or raw[i - 1] != "\\"):
            result.append(raw[i])
            i += 1
            while i < len(raw):
                c = raw[i]
                if c == "\\" and i + 1 < len(raw):
                    result.append(c)
                    result.append(raw[i + 1])
                    i += 2
                    continue
                if c == '"':
                    result.append(c)
                    i += 1
                    break
                # JSON disallows unescaped control chars in strings; replace with space
                if ord(c) < 32:
                    result.append(" ")
                else:
                    result.append(c)
                i += 1
            continue
        result.append(raw[i])
        i += 1
    return "".join(result)


def _recover_truncated_at_position(raw: str, pos: int) -> list[dict] | None:
    """When parse fails at pos (e.g. 63466), try truncating at the last '}' before pos that yields valid JSON."""
    if pos <= 0 or pos > len(raw):
        return None
    # Search backwards from pos-1 for '}' (within last 8k chars to avoid slow scan)
    start = max(0, pos - 8192)
    for i in range(pos - 1, start - 1, -1):
        if raw[i] == "}":
            prefix = raw[: i + 1].rstrip()
            if prefix.endswith(","):
                prefix = prefix[:-1].rstrip()
            if prefix.startswith("["):
                try:
                    data = json.loads(prefix + "]")
                    if isinstance(data, list):
                        return data
                except json.JSONDecodeError:
                    pass
    return None


def _recover_truncated_blocks_json(raw: str) -> list[dict] | None:
    """Recover from truncated free-form blocks JSON (e.g. Expecting value / Unterminated string at ~59k).
    Tries: strip trailing comma and close array; find last complete object and close; close unterminated string."""
    raw = raw.strip()
    if not raw.startswith("["):
        return None

    # 1) "Expecting value" at end: often trailing comma (e.g. ..., "x"},) or valid end with }
    trimmed = raw.rstrip()
    if trimmed.endswith("}"):
        try:
            partial = json.loads(trimmed + "]")
            return partial if isinstance(partial, list) else None
        except json.JSONDecodeError:
            pass
    if trimmed.endswith(","):
        try:
            partial = json.loads(trimmed[:-1] + "]")
            return partial if isinstance(partial, list) else None
        except json.JSONDecodeError:
            pass

    # 2) Find last complete object boundary and close array there
    raw_nl = raw.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    patterns = [
        '"},{"block_type":', '"}, {"block_type":', '"},{"text":', '"}, {"text":',
        '"},\n{"block_type":', '"},\n{"text":', '"},\r\n{"',
        '"},{"', '"}, {"',
    ]
    pos = -1
    for pattern in patterns:
        p = raw.rfind(pattern)
        if p > 0:
            pos = p
            break
        p = raw_nl.rfind(pattern.replace("\n", " ").replace("\r", " "))
        if p > 0:
            pos = p
            break
    if pos <= 0:
        for tail in ('"},"', '"}, "', '"},\n"', '"},\r\n"'):
            p = raw.rfind(tail)
            if p > 0 and p + len(tail) < len(raw):
                next_ch = raw[p + len(tail) : p + len(tail) + 24]
                if "block_type" in next_ch or '"text"' in next_ch:
                    pos = p
                    break
        else:
            pos = -1
    if pos > 0:
        # pos is start of '"},'; include the '}' that closes the object (at pos+1)
        prefix = raw[: pos + 2].rstrip()
        if prefix.endswith(","):
            prefix = prefix[:-1].rstrip()
        try:
            partial = json.loads(prefix + "]")
            return partial if isinstance(partial, list) else None
        except json.JSONDecodeError:
            pass
    # 3) Unterminated string: truncation inside last "text": "..." value — close string, object, array
    fixed = raw.rstrip()
    for suffix in (
        '"}]',   # normal: ..."incomplete  -> ..."incomplete"}]
        '}]',    # string already closed: ..."incomplete"  -> ..."incomplete"}]
    ):
        try:
            to_parse = fixed + suffix
            partial = json.loads(to_parse)
            return partial if isinstance(partial, list) else None
        except json.JSONDecodeError:
            pass
    # Trailing backslash would escape the closing quote; add escaped quote then close
    if fixed.endswith("\\") and not fixed.endswith("\\\\"):
        try:
            partial = json.loads(fixed + '\\""}]')
            return partial if isinstance(partial, list) else None
        except json.JSONDecodeError:
            pass
    return None


def _recover_truncated_slot_json(raw: str, N: int) -> list[dict] | None:
    """If raw is truncated (e.g. at 59k chars), find last complete object, close array, return list of dicts."""
    raw = raw.strip()
    if not raw.startswith("["):
        return None
    # Find last complete object boundary: "}, {"text": (start of next object)
    idx = 0
    last_complete_end = -1
    while True:
        pos = raw.find('"},{"text":', idx)
        if pos == -1:
            break
        last_complete_end = pos + 2  # end of "},
        idx = pos + 1
    if last_complete_end <= 0:
        return None
    prefix = raw[:last_complete_end]
    try:
        partial = json.loads(prefix + "]")
    except json.JSONDecodeError:
        return None
    if not isinstance(partial, list):
        return None
    # Pad to N items
    while len(partial) < N:
        partial.append({"text": ""})
    return partial[:N]


def _extract_text_values_from_json_array(raw: str, expected_count: int) -> list[str] | None:
    """When json.loads fails (e.g. unterminated string), extract "text" values by scanning.
    Looks for \"text\"\\s*:\\s*\" then reads the string value (handling \\ and \") until closing \".
    Returns list of N strings or None if we can't get enough."""
    out = []
    i = 0
    raw = raw.strip()
    # Find array start
    start = raw.find("[")
    if start == -1:
        return None
    i = start + 1
    while i < len(raw) and len(out) < expected_count:
        # Skip whitespace and commas/braces
        while i < len(raw) and raw[i] in " \t\n\r{,}":
            i += 1
        if i >= len(raw):
            break
        # Look for "text" or 'text'
        if raw[i] == '"' and raw[i : i + 6] == '"text"':
            i += 6
        elif raw[i] == "'" and raw[i : i + 6] == "'text'":
            i += 6
        else:
            i += 1
            continue
        # Skip to :
        while i < len(raw) and raw[i] in " \t\n\r":
            i += 1
        if i >= len(raw) or raw[i] != ":":
            continue
        i += 1
        while i < len(raw) and raw[i] in " \t\n\r":
            i += 1
        if i >= len(raw):
            break
        quote = raw[i]
        if quote != '"' and quote != "'":
            continue
        i += 1
        val = []
        while i < len(raw):
            c = raw[i]
            if c == "\\" and i + 1 < len(raw):
                val.append(raw[i + 1])
                i += 2
                continue
            if c == quote:
                i += 1
                break
            if ord(c) < 32:
                val.append(" ")
            else:
                val.append(c)
            i += 1
        out.append("".join(val).strip())
    if len(out) < expected_count:
        while len(out) < expected_count:
            out.append("")
    return out[:expected_count]


def _read_json_string_value(raw: str, i: int) -> tuple[str, int] | None:
    """From position i (after opening quote), read a JSON string value; return (value, next_index) or None."""
    if i >= len(raw) or raw[i] != '"':
        return None
    i += 1
    val = []
    while i < len(raw):
        c = raw[i]
        if c == '\\' and i + 1 < len(raw):
            val.append(raw[i + 1])
            i += 2
            continue
        if c == '"':
            return ("".join(val), i + 1)
        if ord(c) < 32:
            val.append(" ")
        else:
            val.append(c)
        i += 1
    return None


def _extract_blocks_from_malformed_json(raw: str) -> list[dict] | None:
    """When JSON is malformed (e.g. unescaped quote at column 63k), extract block_type+text objects by scanning."""
    raw = raw.strip()
    if not raw.startswith("["):
        return None
    out = []
    i = raw.find("[") + 1
    while i < len(raw):
        # Find start of object
        obj_start = raw.find('{"', i)
        if obj_start == -1:
            obj_start = raw.find("{", i)
        if obj_start == -1:
            break
        i = obj_start + 1
        block_type_val = None
        text_val = None
        # Scan for "block_type": "..." and "text": "..."
        while i < len(raw):
            # Skip whitespace and commas
            while i < len(raw) and raw[i] in " \t\n\r,}":
                if raw[i] == "}":
                    break
                i += 1
            if i >= len(raw) or raw[i] == "}":
                break
            if raw[i] != '"':
                i += 1
                continue
            key = None
            if raw[i : i + 13] == '"block_type"':
                key = "block_type"
                i += 13
            elif raw[i : i + 7] == '"text"':
                key = "text"
                i += 7
            else:
                i += 1
                continue
            while i < len(raw) and raw[i] in " \t\n\r":
                i += 1
            if i >= len(raw) or raw[i] != ":":
                continue
            i += 1
            while i < len(raw) and raw[i] in " \t\n\r":
                i += 1
            parsed = _read_json_string_value(raw, i)
            if parsed is None:
                break
            value, i = parsed
            if key == "block_type":
                block_type_val = value
            else:
                text_val = value
        if block_type_val is not None or text_val is not None:
            out.append({
                "block_type": (block_type_val or "paragraph").strip() or "paragraph",
                "text": (text_val or "").strip(),
            })
    return out if out else None


# Use Google Gemini for LLM formatting
try:
    import google.generativeai as genai
except ImportError:
    genai = None


# Logical block types
LOGICAL_BLOCK_TYPES = (
    "heading",
    "section_header",
    "paragraph",
    "numbered",
    "wherefore",
    "line",
    "signature_line",
    "page_break",
)

SYSTEM_PROMPT = """You format raw text so the output has the same styling and structure as the uploaded template document. The template may be any type (summons, complaint, motion, notice of claim, affidavit, memorandum, letter, etc.).

CRITICAL:
- You MUST NOT invent, rewrite, summarize, or change jurisdiction/venue/party names.
- Output must be a rearrangement/segmentation of the PROVIDED RAW TEXT only.
- If a required slot is not present in the raw text, output an empty string "".

Task: (1) Divide the raw text into sections according to the uploaded document's structure (from the template content and/or images). (2) Match each section with the styling and formatting of the uploaded document: assign the exact template style name as block_type for each segment so the output mirrors the template section-by-section. (3) Output one block per logical segment with the correct block_type (exact template style name or line/signature_line/page_break).

Rules (apply to any document type):
- Match the template: use each style exactly as the template does (title style for main title, section style for section headings, body style for paragraphs, list style for list items).
- One block per logical segment. Preserve exact wording.
- Selective bold/italic/underline within a paragraph: use ** for bold, * for italic, and __ for underline in the text field. Example: against the **CITY OF NEW YORK** (hereinafter as *"respondent"*). Do not bold/italic entire paragraphs; use markers only where the template has selective emphasis. Do not bold words that are merely capitalized (e.g. "CLAIMANT'S" in body text stays regular unless the template bolds it). Italic (*): use for party roles when the template shows them italic (e.g. *Claimant,* *Respondent.*), for *-Against-* when on its own line if the template has it italic, and for defined terms in quotes (e.g. *"respondent"*).
- NOTICE OF CLAIM (and similar claim forms): Bold (**) the document title "NOTICE OF CLAIM", claimant and respondent names (e.g. **ANTHONY SCHEMBRI**, **CITY OF NEW YORK**), **-Against-** when on its own line, "TO:" and addressee name (e.g. **TO: CITY OF NEW YORK**), **PLEASE TAKE NOTICE**, firm and attorney names (e.g. **SEELIG DRESSLER OCHANI, LLC**), key terms like **Personal Injury Action** or **TRIP AND FALL**, dates and addresses when the template emphasizes them (e.g. **NOVEMBER 2, 2025**, **349 EAST 51ST STREET, NEW YORK, NEW YORK 10022**), and section headings (e.g. **1. The name and address of claimant...** or **The nature of the claim:**). Use * only for defined terms in parentheses: *"respondent"*, *"claimant"*. Use __ for underlined phrases when the template underlines (e.g. __that municipal repairs,__). Leave all other body narrative, addresses, phone numbers, and plain capitalized words (e.g. "CLAIMANT'S" in the middle of a sentence) without ** or *.
- Layout: Match the template's alignment. Center-align the starting part of the notice: document title (NOTICE OF CLAIM), "In the Matter of the Claim of:", plaintiff/claimant name, defendant/respondent name, "-Against-", and party roles (Claimant, Respondent). Left-align TO:, addresses, and body. For plaintiff and defendant names on one row (or firm name and claimant name on one row in point 1), use a single tab character between the two so they align (e.g. **SEELIG DRESSLER OCHANI, LLC**\t**ANTHONY SCHEMBRI** or **ANTHONY SCHEMBRI**, Claimant\t**CITY OF NEW YORK**, Respondent). If the template has a two-column layout, output blocks in the same order so content flows correctly.
- Template fidelity (court-grade output): Preserve the template's structure; do not normalize or flatten. (1) Numbering: When the template has explicit numbered clauses (1., 2., 3., 4.)—e.g. "1. The name and post-office address of the claimant...", "2. The nature of the claim:"—output those numbers in the text of each block. Do not convert them to unnumbered headings. (2) Tabs: For tab-separated dual-column layout (e.g. firm name left, claimant name right on one line), use tab characters in the text where the template has column breaks so alignment is preserved. (3) Spacing and hierarchy: Output one block per logical segment in the same order as the template so the engine can apply the template's spacing, indentation, and alignment. (4) Separator lines: Use block_type line with the exact line characters from the template; keep centered separators as their own block so spacing is preserved.
- Case number / date / caption -> use the template style used for that in the template (often right-aligned).
- Addresses -> same style as in template; one block per line if the template breaks them.
- Signature block -> use the template style; use block_type signature_line for the underline line.
- Separator lines (dashes/dots ending in X) -> block_type line, exact line characters in text.
- Section underlines (solid line under a cause of action or heading) -> block_type section_underline, empty text "".
- Page breaks -> output page_break (empty text) before each major section that starts on a new page in the template. Look at the template structure to see where sections begin. If the raw text contains a line of six or more backticks (e.g. ``````), treat it as an explicit page break: output a block with block_type page_break and empty text at that position; do not include the backtick line in any content block.
- Motion packs (Notice of Motion + Affirmation + Affidavit): output all documents in order. Each document has a caption (court, county, parties, index no., document title like NOTICE OF MOTION TO RESTORE / AFFIRMATION IN SUPPORT / AFFIDAVIT OF SERVICE) then body. Use the same template styles for caption and body as in the template. Do not merge multiple documents into one; keep each document's caption and body as separate blocks in sequence.
- Checkboxes: Use [ ] and [x] in text; they render as checkbox symbols.

Numbered allegations (e.g. under FIRST CAUSE OF ACTION / NEGLIGENCE):
- Output each allegation as a SEPARATE block. One block per "That on...", "That the...", "By reason of...", "Pursuant to...", "Plaintiff's damages...", etc. Do not merge multiple allegations into one block.
- Assign the template's list/numbered style (from the style guide) as block_type for each such allegation so the document engine can apply numbering dynamically from the template.
- Do NOT add numbers or letters in the text (no "1.", "2.", "3.", "a.", "b."). Numbering is applied by the engine from the uploaded template; your job is to segment and assign the list style.
- Output the ENTIRE document. Do not stop early: include all sections through the end. Every part of the raw text must appear in the output.

Complaint structure (when present in raw text)—output each as separate blocks with the template's styles:
- WHEREFORE clause (e.g. "WHEREFORE, Plaintiff demands judgment...") -> one block; then each demand for relief ("For compensatory damages...", "For costs and disbursements...", "For such other and further relief...") as its own block using the template's body or list style (do not add "1." "2." in text).
- Jury demand (e.g. "Plaintiff hereby demands a trial by jury...") -> separate block.
- Dated + signature block (Dated: ... [Attorney Name], ESQ., Law Firm, Attorneys for Plaintiff, address, phone) -> use template styles; use signature_line for the underline.
- Attorney verification (if present) -> separate blocks for the heading, body paragraphs, and signature. Use the same styles as in the template for verification.
- The document does NOT end at the first attorney signature. If the raw text continues after "Yours, etc.;" or after the first signature block with any of: WHEREFORE and demands for relief, another caption (e.g. MUMUNI AHMED, Index No.:), verification (ATTORNEY'S VERIFICATION / affirms under penalties of perjury), SUMMONS AND VERIFIED COMPLAINT footer, certification (22 NYCRR 130-1.1(c)), Service of a copy... admitted, or NOTICE OF ENTRY—you MUST output blocks for every one of those sections. Continue until the very end of the raw text.

Reply with a JSON array only. Each element: {"block_type": "<exact style name from template or line/signature_line/page_break>", "text": "<content>"}. In text use ** for bold, * for italic, __ for underline only where the template has selective emphasis; do not wrap whole paragraphs in ** or *."""


def _call_openai(
    text: str,
    style_schema: dict,
    template_page_images: list[str] | None = None,
    template_page_ocr_texts: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Call Gemini API; returns list of (block_type, text).
    template_page_images: optional list of base64 PNG strings (template pages) for vision.
    template_page_ocr_texts: optional OCR text per page (Tesseract) for image-heavy/scanned docs."""
    return _call_gemini(text, style_schema, template_page_images, template_page_ocr_texts)


def _parse_blocks_response(raw: str) -> list[tuple[str, str]]:
    """Parse LLM response (JSON array of block_type/text) into list of (block_type, text)."""
    raw = _strip_llm_refusal_artifact(raw)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    raw = _sanitize_json_control_chars(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raw_fallback = re.sub(r"[\x00-\x1f]", " ", raw)
        data = None
        try:
            data = json.loads(raw_fallback)
        except json.JSONDecodeError as e2:
            pos = getattr(e2, "pos", None)
            if pos is not None and pos > 0:
                prefix = raw_fallback[:pos].rstrip()
                if prefix.endswith(","):
                    prefix = prefix[:-1].rstrip()
                if prefix.startswith("[") and prefix.endswith("}"):
                    try:
                        data = json.loads(prefix + "]")
                    except json.JSONDecodeError:
                        pass
                if data is None:
                    data = _recover_truncated_at_position(raw_fallback, pos)
            if data is None:
                data = _recover_truncated_blocks_json(raw_fallback)
            if data is None:
                data = _recover_truncated_blocks_json(raw)
            if data is None:
                data = _extract_blocks_from_malformed_json(raw_fallback)
            if data is None:
                raise
    out = []
    for item in data:
        bt = (item.get("block_type") or "paragraph").strip()
        if not bt:
            bt = "paragraph"
        out.append((bt, item.get("text", "").strip()))
    return out


def _call_gemini(
    text: str,
    style_schema: dict,
    template_page_images: list[str] | None = None,
    template_page_ocr_texts: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Call Google Gemini API; same contract as _call_openai. Uses GEMINI_API_KEY/GOOGLE_API_KEY and GEMINI_MODEL/FORMATTER_LLM_MODEL."""
    if genai is None:
        raise RuntimeError("Gemini requested but google-generativeai not installed. pip install google-generativeai")

    formatting_instructions = (style_schema.get("formatting_instructions") or "").strip()
    if not formatting_instructions:
        style_guide = (style_schema.get("style_guide") or style_schema.get("style_guide_markdown") or "").strip()
        if not style_guide:
            style_list = style_schema.get("paragraph_style_names", []) or list(style_schema.get("style_map", {}).values())
            style_guide = "Style names: " + ", ".join(style_list)
        template_content_for_prompt = style_schema.get("template_content", [])
        style_formatting = style_schema.get("style_formatting", {})
        section_prompts = build_section_formatting_prompts(template_content_for_prompt, style_formatting)
        formatting_instructions = style_guide + ("\n\n" + section_prompts if section_prompts else "")

    line_samples = style_schema.get("line_samples", [])
    line_note = ""
    if line_samples:
        examples = [s.get("text", "")[:60] + ("..." if len(s.get("text", "")) > 60 else "") for s in line_samples[:5]]
        line_note = f"\nLine/separator samples (block_type 'line' or 'signature_line'): {examples}\n"

    template_structure = style_schema.get("template_structure", [])
    section_structure_block = ""
    if template_structure:
        lines = []
        for i, spec in enumerate(template_structure[:80]):
            style_name = spec.get("style") or "Normal"
            section_type = spec.get("section_type") or "body"
            hint = (spec.get("hint") or spec.get("text") or "")[:60]
            if hint:
                lines.append(f"  {i + 1}. [{style_name}] ({section_type}): {hint}")
            else:
                lines.append(f"  {i + 1}. [{style_name}] ({section_type})")
        section_structure_block = (
            "Section structure of the uploaded document (divide raw text into these sections; use the style in brackets as block_type for each):\n"
            + "\n".join(lines)
            + "\n\n"
        )

    template_content = style_schema.get("template_content", [])
    template_section = ""
    if template_content:
        lines = []
        for item in template_content:
            style_name = item.get("style") or "Normal"
            para_text = (item.get("text") or "").strip()
            lines.append(f"[{style_name}]: {para_text}" if para_text else f"[{style_name}]:")
        template_section = "Template document (each paragraph with its style name):\n" + "\n".join(lines) + "\n\n"

    ocr_texts = template_page_ocr_texts if template_page_ocr_texts is not None else (style_schema.get("template_page_ocr_texts") or [])
    ocr_block = ""
    if ocr_texts and any(t.strip() for t in ocr_texts):
        lines = [f"Page {i + 1} (OCR):\n{t}" for i, t in enumerate(ocr_texts) if t.strip()]
        if lines:
            ocr_block = "OCR text extracted from template pages (use for layout/structure reference):\n\n" + "\n\n".join(lines) + "\n\n"

    user_text = f"""{section_structure_block}{template_section}{ocr_block}Formatting instructions (use these exact style names as block_type):

{formatting_instructions}
{line_note}

---

Raw text to format. (1) Divide it into sections according to the uploaded document structure above. (2) Match each section with the styling and formatting of the uploaded document: assign the block_type (style name) that the template uses for that section. Use the same styles for titles, section headings, body paragraphs, and lists as in the template. For causes of action (e.g. negligence): output each allegation (each "That on...", "By reason of...", etc.) as a separate block with the template's list/numbered style; do not add "1." or "2." in the text—numbering is applied from the template. Insert page_break where the template starts a new section on a new page. If the raw text contains a line of six or more backticks (e.g. ``````), output a block with block_type page_break and empty text at that position; do not include the backtick line in any content block. Include every part of the raw text to the very end—do not stop after the first signature block; if WHEREFORE, verification, SUMMONS AND VERIFIED COMPLAINT, certification, or NOTICE OF ENTRY appear later in the raw text, output blocks for all of them. In the text field use ** for bold, * for italic, and __ for underline only where the template has selective emphasis. Do not bold/italic entire paragraphs or plain capitalized words. Output a JSON array of {{"block_type": "<style name or line/signature_line/page_break>", "text": "<content>"}}.

---
{text}
---"""

    page_images = template_page_images if template_page_images is not None else (style_schema.get("template_page_images") or [])
    parts = []
    if page_images:
        vision_instruction = (
            "Formatting must follow the uploaded template document. The following images are each page of that template (Page 1, Page 2, ...). "
            "First, identify the sections of the template from these images (e.g. caption, court/parties, headings, body, signature). "
            "From the template images, infer styling: (1) Which text is bold—e.g. document title (NOTICE OF CLAIM), labels (Claimant, Respondents, -against-), PLEASE TAKE NOTICE, section headings (The nature of the claim:, The items of damage or injuries claimed are:), firm and attorney names. Use ** around those in the text field. (2) Alignment: center the main document title; left-align caption lines (court, parties, TO:, addresses); justify body paragraphs; for firm name and claimant name on one line (or plaintiff/defendant), use a single tab between them. "
            "Then divide the raw text into sections that correspond to the template's sections. "
            "Match each section with the styling and formatting of the uploaded document: assign block_type (style name) and segment the raw text "
            "so the output mirrors the template section-by-section in layout, spacing, indentation, and style. Then use the style guide and raw text below.\n\n"
            + "Template pages (use these for formatting reference):\n\n"
        )
        parts.append(vision_instruction)
        for i, b64 in enumerate(page_images):
            parts.append(f"--- Page {i + 1} ---\n")
            parts.append({
                "inline_data": {
                    "mime_type": "image/png",
                    "data": base64.b64decode(b64),
                }
            })
        parts.append("\n\n" + user_text)
    else:
        parts.append(user_text)

    api_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
    )
    if not api_key:
        raise ValueError("Gemini requested but GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    genai.configure(api_key=api_key)
    model_name = (
        os.environ.get("GEMINI_MODEL", "").strip()
        or os.environ.get("FORMATTER_LLM_MODEL", "gemini-1.5-pro").strip()
    )
    max_tokens = int(os.environ.get("FORMATTER_LLM_MAX_TOKENS", "16384"))

    model = genai.GenerativeModel(
        model_name,
        system_instruction=SYSTEM_PROMPT,
    )
    config = genai.types.GenerationConfig(
        temperature=0.0,
        max_output_tokens=max_tokens,
    )
    response = model.generate_content(parts, generation_config=config)
    if not response.text:
        raise RuntimeError("Gemini returned empty response")
    return _parse_blocks_response(response.text)


SLOT_FILL_SYSTEM = """STRICT SLOT MAPPING PROMPT
You are a document segmentation engine.

You MUST NOT generate new legal content.
You MUST NOT rewrite text.
You MUST NOT duplicate sections.
You MUST NOT invent pleadings.

Your job is ONLY to:
- find matching text in the provided raw document
- assign that text to the correct template slot

Rules:
- If text does not exist → return ""
- Preserve wording exactly
- Do not expand or summarize
- For [line/separator] and [signature underline] slots always output ""

Section discipline: Caption (court header), NOTICE blocks, PROOF OF SERVICE, WHEREFORE — assign each section type once; do not duplicate.

Output JSON only: one object per template slot with a "text" field. Example: [{"text": "..."}, {"text": ""}, ...]."""


# Section-specific instructions for multi-agent slot-fill (appended to SLOT_FILL_SYSTEM per section)
SECTION_SYSTEM_PROMPTS = {
    "caption": (
        " You are filling ONLY CAPTION slots: court name, county, parties, -against-, index no., document title (e.g. NOTICE OF MOTION TO RESTORE). "
        "Find the matching text in the raw document and assign to the correct slot. Use empty string for line/separator slots."
    ),
    "motion_notice": (
        " You are filling ONLY MOTION NOTICE / body-of-notice slots (e.g. PLEASE TAKE NOTICE, motion text). "
        "Do not put caption or attorney signature content here. Use empty string for line/separator slots."
    ),
    "body": (
        " You are filling ONLY BODY slots (narrative, allegations, WHEREFORE, demands for relief). "
        "One block per allegation; do not add '1.' '2.' in text—numbering is applied by the template. Use empty string for line/section_underline slots."
    ),
    "attorney_signature": (
        " You are filling ONLY ATTORNEY SIGNATURE slots: Dated, attorney name, firm, Attorneys for Plaintiff/Defendant, address, phone. "
        "Preserve spacing and line structure. Use empty string for signature underline slots."
    ),
    "notary": (
        " You are filling ONLY NOTARY / jurat slots (Sworn to before me, Notary Public, State of, County of). "
        "Preserve wording exactly. Use empty string for signature line slots."
    ),
    "to_section": (
        " You are filling ONLY TO: / recipient slots (TO:, firm name, address lines). "
        "One slot per line if the template has multiple. Use empty string for separator slots."
    ),
    "affirmation": (
        " You are filling ONLY AFFIRMATION slots (e.g. affirms the following, respectfully submitted, WHEREFORE in affirmation). "
        "Preserve structure. Use empty string for line/section_underline slots."
    ),
    "affidavit": (
        " You are filling ONLY AFFIDAVIT slots (duly sworn, under penalties of perjury, body paragraphs). "
        "Use empty string for signature line slots."
    ),
    "separator": (
        " You are filling ONLY separator/line slots. Output empty string for each slot unless the template line characters are to be preserved."
    ),
}


def _get_section_system_prompt(section_type: str) -> str:
    """Return full system prompt for one section (SLOT_FILL_SYSTEM + section-specific lines)."""
    extra = SECTION_SYSTEM_PROMPTS.get(section_type, SECTION_SYSTEM_PROMPTS["body"])
    return SLOT_FILL_SYSTEM + extra


def _call_gemini_slot_fill_one_section(
    text: str,
    style_schema: dict,
    start: int,
    end: int,
    template_structure: list[dict],
) -> list[str]:
    """Call Gemini to fill slots for one section. Same contract as _call_openai_slot_fill_one_section."""
    if genai is None:
        raise RuntimeError("Gemini requested but google-generativeai not installed. pip install google-generativeai")
    if start >= end:
        return []
    section_specs = template_structure[start:end]
    section_type = section_specs[0].get("section_type", "body") if section_specs else "body"
    N_local = end - start

    block_descriptions = []
    for local_i, spec in enumerate(section_specs):
        kind = spec.get("block_kind", "paragraph")
        style = spec.get("style", "Normal")
        hint = (spec.get("hint") or "")[:100]
        st = spec.get("section_type", "body")
        if kind == "line":
            block_descriptions.append(f"Slot {local_i}: [{st}] [line/separator]. Use empty string.")
        elif kind == "signature_line":
            block_descriptions.append(f"Slot {local_i}: [{st}] [signature underline]. Use empty string.")
        elif kind == "section_underline":
            block_descriptions.append(f"Slot {local_i}: [{st}] [section underline]. Use empty string.")
        else:
            block_descriptions.append(f"Slot {local_i}: [{st}] style={style}. Hint: \"{hint}\"")
    blocks_desc = "\n".join(block_descriptions)

    user_content = f"""You are filling ONLY the slots for the "{section_type.upper()}" section (slots {start}–{end - 1} of the full document). The raw document is below—find the text that belongs in THIS section and assign it to the correct slot. Output a JSON array of exactly {N_local} objects: [{{\"text\": \"...\"}}, ...].

Slots for this section:
{blocks_desc}

For [line/separator], [signature underline], and [section underline] use empty string "".

Raw text:
---
{text}
---"""

    api_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
    )
    if not api_key:
        raise ValueError("Gemini requested but GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    genai.configure(api_key=api_key)
    model_name = (
        os.environ.get("GEMINI_MODEL", "").strip()
        or os.environ.get("FORMATTER_LLM_MODEL", "gemini-1.5-pro").strip()
    )
    max_tokens = int(os.environ.get("FORMATTER_LLM_MAX_TOKENS", "16384"))
    system_prompt = _get_section_system_prompt(section_type)

    model = genai.GenerativeModel(model_name, system_instruction=system_prompt)
    config = genai.types.GenerationConfig(temperature=0.0, max_output_tokens=max_tokens)
    response = model.generate_content(user_content, generation_config=config)
    if not response.text:
        raise RuntimeError("Gemini returned empty response")
    raw = response.text.strip()
    raw = _strip_llm_refusal_artifact(raw)
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    raw = _sanitize_json_control_chars(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raw_fallback = re.sub(r"[\x00-\x1f]", " ", raw)
        try:
            data = json.loads(raw_fallback)
        except json.JSONDecodeError:
            extracted = _extract_text_values_from_json_array(raw_fallback, N_local)
            if extracted is not None:
                return extracted[:N_local]
            extracted = _extract_text_values_from_json_array(raw, N_local)
            if extracted is not None:
                return extracted[:N_local]
            data = _recover_truncated_slot_json(raw_fallback, N_local)
            if data is None:
                data = _recover_truncated_slot_json(raw, N_local)
            if data is None:
                raise
    out = []
    for i, item in enumerate(data):
        if i >= N_local:
            break
        t = (item.get("text") or "").strip() if isinstance(item, dict) else ""
        out.append(t)
    while len(out) < N_local:
        out.append("")
    return out[:N_local]


def _call_openai_slot_fill_one_section(
    text: str,
    style_schema: dict,
    start: int,
    end: int,
    template_structure: list[dict],
) -> list[str]:
    """Call Gemini to fill slots for one section (indices [start:end]). Returns list of (end - start) text strings."""
    if start >= end:
        return []
    return _call_gemini_slot_fill_one_section(text, style_schema, start, end, template_structure)


def _slot_fill_by_section(text: str, style_schema: dict) -> list[str]:
    """Multi-agent slot-fill: one LLM call per section, parallel execution, merge in order. Returns list of N slot texts."""
    template_structure = style_schema.get("template_structure") or []
    if not template_structure:
        return []
    N = len(template_structure)

    # Build section ranges (section_type, start, end)
    section_ranges = []
    i = 0
    while i < N:
        st = template_structure[i].get("section_type", "body")
        start = i
        while i < N and template_structure[i].get("section_type") == st:
            i += 1
        section_ranges.append((st, start, i))

    # Optional: skip API for sections that are only line/signature_line/section_underline (all empty)
    def section_is_all_special(s_start: int, s_end: int) -> bool:
        for j in range(s_start, s_end):
            k = template_structure[j].get("block_kind", "paragraph")
            if k not in ("line", "signature_line", "section_underline"):
                return False
        return True

    max_workers = int(os.environ.get("FORMATTER_MULTI_AGENT_MAX_WORKERS", "5"))
    max_workers = max(1, min(max_workers, 10))

    def run_section(args):
        st, start, end = args
        if section_is_all_special(start, end):
            return [""] * (end - start)
        return _call_openai_slot_fill_one_section(text, style_schema, start, end, template_structure)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        section_results = list(executor.map(run_section, section_ranges))

    slot_texts = []
    for texts in section_results:
        slot_texts.extend(texts)
    while len(slot_texts) < N:
        slot_texts.append("")
    return slot_texts[:N]


def _call_gemini_slot_fill(text: str, style_schema: dict) -> list[str]:
    """Call Gemini to fill N slots. Same contract as _call_openai_slot_fill."""
    if genai is None:
        raise RuntimeError("Gemini requested but google-generativeai not installed. pip install google-generativeai")
    template_structure = style_schema.get("template_structure") or []
    if not template_structure:
        return []
    N = len(template_structure)
    block_descriptions = []
    section_ranges = []
    i = 0
    while i < N:
        st = template_structure[i].get("section_type", "body")
        start = i
        while i < N and template_structure[i].get("section_type") == st:
            i += 1
        section_ranges.append((st, start, i))
    section_summary = "\n".join(
        f"  Blocks {s}-{e-1}: {st.upper()}" for st, s, e in section_ranges
    )
    for i, spec in enumerate(template_structure):
        kind = spec.get("block_kind", "paragraph")
        style = spec.get("style", "Normal")
        hint = spec.get("hint", "")[:100]
        st = spec.get("section_type", "body")
        if kind == "line":
            block_descriptions.append(f"Block {i}: [{st}] [line/separator]. Use empty string.")
        elif kind == "signature_line":
            block_descriptions.append(f"Block {i}: [{st}] [signature underline]. Use empty string.")
        elif kind == "section_underline":
            block_descriptions.append(f"Block {i}: [{st}] [section underline]. Use empty string.")
        else:
            block_descriptions.append(f"Block {i}: [{st}] style={style}. Hint: \"{hint}\"")
    blocks_desc = "\n".join(block_descriptions)

    user_content = f"""CRITICAL — IGNORE THE ORDER OF THE RAW TEXT. The raw text below may list sections in any order (e.g. "Dated...", "TO:", or attorney block first). You MUST ignore that order. The template's first blocks are CAPTION. Fill slots by SECTION and MEANING only:

• Find "SUPREME COURT OF THE STATE OF NEW YORK" and "COUNTY OF ORANGE" → put in CAPTION slots whose hint is court/county.
• Find "ROSEANN COZZUPOLI", "Plaintiff,", "-against-", defendants, "Defendants." → put in CAPTION party slots.
• Find "Index no." and "NOTICE OF MOTION TO RESTORE" → put in CAPTION slots (index/title).
• Find "PLEASE TAKE NOTICE" and the motion body → put ONLY in MOTION_NOTICE slots.
• Find "Dated:", "December ____, 2025", "DAVID E. SILVERMAN", "RAPHAELSON & LEVINE", "Attorneys for Plaintiff", address, phone → put ONLY in ATTORNEY_SIGNATURE slots.
• Find "TO:" and each recipient (firm, address) → put ONLY in TO_SECTION slots.
• Do NOT put attorney or TO content in caption slots. Do NOT put caption content in attorney or body slots. Each piece of content goes in exactly ONE slot.

Template section order:
{section_summary}

Block list:
{blocks_desc}

For [line/separator], [signature underline], and [section underline] use empty string "". Output a JSON array of exactly {N} objects: [{{\"text\": \"...\"}}, {{\"text\": \"\"}}, ...].

If the raw text contains a line of six or more backticks (e.g. ``````), do not include that line in any slot; omit it from your output.

Raw text:
---
{text}
---"""

    api_key = (
        os.environ.get("GEMINI_API_KEY", "").strip()
    )
    if not api_key:
        raise ValueError("Gemini requested but GEMINI_API_KEY (or GOOGLE_API_KEY) is not set")
    genai.configure(api_key=api_key)
    model_name = (
        os.environ.get("GEMINI_MODEL", "").strip()
        or os.environ.get("FORMATTER_LLM_MODEL", "gemini-1.5-pro").strip()
    )
    model = genai.GenerativeModel(model_name, system_instruction=SLOT_FILL_SYSTEM)
    config = genai.types.GenerationConfig(temperature=0.0, max_output_tokens=16384)
    response = model.generate_content(user_content, generation_config=config)
    if not response.text:
        raise RuntimeError("Gemini returned empty response")
    raw = response.text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    raw = _sanitize_json_control_chars(raw)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raw_fallback = re.sub(r"[\x00-\x1f]", " ", raw)
        try:
            data = json.loads(raw_fallback)
        except json.JSONDecodeError:
            extracted = _extract_text_values_from_json_array(raw_fallback, N)
            if extracted is not None:
                return extracted
            extracted = _extract_text_values_from_json_array(raw, N)
            if extracted is not None:
                return extracted
            data = _recover_truncated_slot_json(raw_fallback, N)
            if data is None:
                data = _recover_truncated_slot_json(raw, N)
            if data is None:
                raise
    out = []
    for i, item in enumerate(data):
        if i >= N:
            break
        t = (item.get("text") or "").strip() if isinstance(item, dict) else ""
        out.append(t)
    while len(out) < N:
        out.append("")
    return out[:N]


def _call_openai_slot_fill(text: str, style_schema: dict) -> list[str]:
    """Call Gemini to fill N slots from template_structure. Returns list of N text strings."""
    template_structure = style_schema.get("template_structure") or []
    if not template_structure:
        return []
    return _call_gemini_slot_fill(text, style_schema)


def format_text_with_llm(
    text: str,
    style_schema: dict,
    use_slot_fill: bool = True,
    template_page_images: list[str] | None = None,
    template_page_ocr_texts: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Use LLM to convert raw text into list of (block_type, text).
    When use_slot_fill=True and template_structure exists: fill exactly N slots (template limits output length).
    When use_slot_fill=False or no template_structure: segment entire text into blocks (all content rendered).
    template_page_images: optional list of base64 PNG strings (one per template page) for vision.
    template_page_ocr_texts: optional OCR text per page (Tesseract) for layout/structure reference."""
    # Remove refusal artifact from INPUT so WHEREFORE, signature, verification etc. are all formatted (not cut off)
    text = _strip_llm_refusal_artifact(text or "")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()  # collapse excess newlines left after removal
    template_structure = style_schema.get("template_structure") if use_slot_fill else None
    if template_structure:
        use_multi_agent = os.environ.get("FORMATTER_MULTI_AGENT", "").strip().lower() in ("1", "true", "yes")
        if use_multi_agent:
            slot_texts = _slot_fill_by_section(text, style_schema)
        else:
            slot_texts = _call_openai_slot_fill(text, style_schema)
        # Strip any page-break marker that slipped through so it does not appear in output
        slot_texts = [_strip_page_break_marker_in_text(slot_texts[i] if i < len(slot_texts) else "") for i in range(len(template_structure))]
        # Return (style, text) per slot so formatter can use exact template structure
        return [
            (template_structure[i].get("style", "Normal"), slot_texts[i])
            for i in range(len(template_structure))
        ]

    # One-image-per-minute mode: one request per template page, 1 min apart, then merge (for strict rate limiting)
    page_images = template_page_images if template_page_images is not None else (style_schema.get("template_page_images") or [])
    ocr_texts = template_page_ocr_texts if template_page_ocr_texts is not None else (style_schema.get("template_page_ocr_texts") or [])
    use_one_image_per_minute = (
        len(page_images) > 1
        and os.environ.get("FORMATTER_ONE_IMAGE_PER_MINUTE", "").strip().lower() in ("1", "true", "yes")
    )
    if use_one_image_per_minute:
        delay_sec = int(os.environ.get("FORMATTER_ONE_IMAGE_PER_MINUTE_DELAY_SECONDS", "60"))
        n = len(page_images)
        chunks = _split_text_into_chunks(text, n)
        all_blocks = []
        for i in range(n):
            chunk = chunks[i].strip() if i < len(chunks) else ""
            if chunk:
                logging.info("One-image-per-minute: sending page %s/%s to LLM", i + 1, n)
                page_imgs = [page_images[i]]
                page_ocr = [ocr_texts[i]] if i < len(ocr_texts) and ocr_texts else []
                blocks = _call_openai(
                    chunk,
                    style_schema,
                    template_page_images=page_imgs,
                    template_page_ocr_texts=page_ocr if page_ocr else None,
                )
                all_blocks.extend(blocks)
            if i < n - 1:
                all_blocks.append(("page_break", ""))
                if delay_sec > 0:
                    logging.info("One-image-per-minute: waiting %s seconds before next page", delay_sec)
                    time.sleep(delay_sec)
        return _expand_page_break_markers(all_blocks)

    blocks = _call_openai(
        text,
        style_schema,
        template_page_images=template_page_images,
        template_page_ocr_texts=template_page_ocr_texts,
    )
    return _expand_page_break_markers(blocks)
