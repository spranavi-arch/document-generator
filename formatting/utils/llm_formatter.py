"""Use an LLM to split and label text into styled blocks (block_type + text).
Formatting-only: no docgen prompts or section logic; input is raw text + style schema from template."""

import json
import os
import re

from utils.style_extractor import build_section_formatting_prompts


def _repair_unescaped_quotes_in_json(raw: str) -> str:
    """Escape unescaped double quotes inside JSON string values (e.g. "text": "He said "no"" -> ... "He said \\"no\\"").
    Fixes 'Expecting property name enclosed in double quotes' when the LLM puts raw quotes in text values."""
    result = []
    i = 0
    in_string = False
    while i < len(raw):
        c = raw[i]
        if not in_string:
            result.append(c)
            if c == '"':
                in_string = True
            i += 1
            continue
        if c == "\\" and i + 1 < len(raw):
            result.append(c)
            result.append(raw[i + 1])
            i += 2
            continue
        if c == '"':
            next_i = i + 1
            while next_i < len(raw) and raw[next_i] in " \t\n\r":
                next_i += 1
            next_c = raw[next_i] if next_i < len(raw) else ""
            if next_c in ",:}]" or next_c == "" or (next_c == '"' and next_i < len(raw)):
                result.append(c)
                in_string = False
            else:
                result.append("\\")
                result.append('"')
            i += 1
            continue
        result.append(c)
        i += 1
    return "".join(result)


def _repair_trailing_commas_json(raw: str) -> str:
    """Remove trailing commas before ] or } to fix 'Expecting property name' from malformed JSON."""
    return re.sub(r",\s*]", "]", re.sub(r",\s*}", "}", raw))


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

# Optional: use OpenAI or Azure OpenAI
try:
    from openai import AzureOpenAI, OpenAI
except ImportError:
    AzureOpenAI = None
    OpenAI = None

# Logical block types (fallbacks when block_type is not a template style name)
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

SYSTEM_PROMPT = """You format raw text so the output has the same styling and structure as the uploaded template document. The template may be any type (summons, complaint, motion, memorandum, letter, etc.).

CRITICAL:
- You MUST NOT invent, rewrite, summarize, or change jurisdiction/venue/party names.
- Output must be a rearrangement/segmentation of the PROVIDED RAW TEXT only.
- If a required slot is not present in the raw text, output an empty string "".

Task: (1) Use the template structure and style guide below to label every part of the raw text. (2) Output one block per logical segment with the correct block_type (exact template style name or line/signature_line/page_break).

Rules (apply to any document type):
- Match the template: use each style exactly as the template does (title style for main title, section style for section headings, body style for paragraphs, list style for list items).
- One block per logical segment. Preserve exact wording. Output plain text only (no markdown).
- Case number / date / caption -> use the template style used for that in the template (often right-aligned).
- Addresses -> same style as in template; one block per line if the template breaks them.
- Signature block -> use the template style; use block_type signature_line for the underline line.
- Separator lines (dashes/dots ending in X) -> block_type line, exact line characters in text.
- Section underlines (solid line under a cause of action or heading) -> block_type section_underline, empty text "".
- Page breaks -> output page_break (empty text) before each major section that starts on a new page in the template. Look at the template structure to see where sections begin.
- Motion packs (Notice of Motion + Affirmation + Affidavit): output all documents in order. Each document has a caption (court, county, parties, index no., document title like NOTICE OF MOTION TO RESTORE / AFFIRMATION IN SUPPORT / AFFIDAVIT OF SERVICE) then body. Use the same template styles for caption and body as in the template. Do not merge multiple documents into one; keep each document's caption and body as separate blocks in sequence.
- Checkboxes: Use [ ] and [x] in text; they render as checkbox symbols.

Reply with a JSON array only. Each element: {"block_type": "<exact style name from template or line/signature_line/page_break>", "text": "<content>"}."""


def build_formatting_prompt_preview(
    text: str,
    style_schema: dict,
    template_page_images: list[str] | None = None,
    template_page_ocr_texts: list[str] | None = None,
) -> dict:
    """
    Build the exact system and user messages that would be sent to the formatting LLM (no API call).
    Returns {"system": str, "user": str} for display in UI or debugging.
    When template has page images, user message shows placeholders "[Image: Page N]" instead of base64.
    """
    style_guide = (style_schema.get("style_guide") or style_schema.get("style_guide_markdown") or "").strip()
    if not style_guide:
        style_list = style_schema.get("paragraph_style_names", []) or list(style_schema.get("style_map", {}).values())
        style_guide = "Style names: " + ", ".join(style_list)

    line_samples = style_schema.get("line_samples", [])
    line_note = ""
    if line_samples:
        examples = [s.get("text", "")[:60] + ("..." if len(s.get("text", "")) > 60 else "") for s in line_samples[:5]]
        line_note = f"\nLine/separator samples (block_type 'line' or 'signature_line'): {examples}\n"

    template_content = style_schema.get("template_content", [])
    template_section = ""
    if template_content:
        lines = []
        for item in template_content:
            style_name = item.get("style") or "Normal"
            para_text = (item.get("text") or "").strip()
            lines.append(f"[{style_name}]: {para_text}" if para_text else f"[{style_name}]:")
        template_section = "Template document (each paragraph with its style name):\n" + "\n".join(lines) + "\n\n"

    style_formatting = style_schema.get("style_formatting", {})
    section_prompts = build_section_formatting_prompts(template_content, style_formatting)
    section_block = section_prompts + "\n\n" if section_prompts else ""

    ocr_texts = template_page_ocr_texts if template_page_ocr_texts is not None else (style_schema.get("template_page_ocr_texts") or [])
    ocr_block = ""
    if ocr_texts and any(t.strip() for t in ocr_texts):
        lines = [f"Page {i + 1} (OCR):\n{t}" for i, t in enumerate(ocr_texts) if t.strip()]
        if lines:
            ocr_block = "OCR text extracted from template pages (use for layout/structure reference):\n\n" + "\n\n".join(lines) + "\n\n"

    user_text = f"""{template_section}{section_block}{ocr_block}Extracted style guide (use these exact style names as block_type):

{style_guide}
{line_note}

---

Raw text to format. Match the template structure above: use the same styles for titles, section headings, body paragraphs, and lists as in the template. Insert page_break where the template starts a new section on a new page. Output plain text only. Output a JSON array of {{"block_type": "<style name or line/signature_line/page_break>", "text": "<content>"}}.

---
{text}
---"""

    page_images = template_page_images if template_page_images is not None else (style_schema.get("template_page_images") or [])
    if page_images:
        vision_instruction = (
            "Formatting must follow the uploaded template document. The following images are each page of that template (Page 1, Page 2, ...). "
            "Use these images as your primary reference for how to format the raw text: match the layout, spacing, indentation, headings, captions, "
            "section structure, and placement of content on the page. Assign block_type and segment the raw text so the output document matches "
            "the visual structure of these template pages. Then use the style guide and raw text below.\n\n"
        )
        image_placeholders = "\n".join(f"[Image: Page {i + 1}]" for i in range(len(page_images)))
        user_display = vision_instruction + "Template pages (use these for formatting reference):\n\n" + image_placeholders + "\n\n" + user_text
    else:
        user_display = user_text

    return {"system": SYSTEM_PROMPT, "user": user_display}


def _call_openai(
    text: str,
    style_schema: dict,
    template_page_images: list[str] | None = None,
    template_page_ocr_texts: list[str] | None = None,
) -> list[tuple[str, str]]:
    """Call OpenAI or Azure OpenAI API; returns list of (block_type, text).
    template_page_images: optional list of base64 PNG strings (template pages) for vision.
    template_page_ocr_texts: optional OCR text per page (Tesseract) for image-heavy/scanned docs."""
    if not OpenAI and not AzureOpenAI:
        raise RuntimeError("openai package not installed. pip install openai")

    style_guide = (style_schema.get("style_guide") or style_schema.get("style_guide_markdown") or "").strip()
    if not style_guide:
        style_list = style_schema.get("paragraph_style_names", []) or list(style_schema.get("style_map", {}).values())
        style_guide = "Style names: " + ", ".join(style_list)

    line_samples = style_schema.get("line_samples", [])
    line_note = ""
    if line_samples:
        examples = [s.get("text", "")[:60] + ("..." if len(s.get("text", "")) > 60 else "") for s in line_samples[:5]]
        line_note = f"\nLine/separator samples (block_type 'line' or 'signature_line'): {examples}\n"

    # Template content: how each paragraph was styled in the uploaded DOCX (so LLM can extract and apply formatting)
    template_content = style_schema.get("template_content", [])
    template_section = ""
    if template_content:
        lines = []
        for item in template_content:
            style_name = item.get("style") or "Normal"
            para_text = (item.get("text") or "").strip()
            lines.append(f"[{style_name}]: {para_text}" if para_text else f"[{style_name}]:")
        template_section = "Template document (each paragraph with its style name):\n" + "\n".join(lines) + "\n\n"

    # Per-section formatting prompts (dynamic prompt for each section type)
    style_formatting = style_schema.get("style_formatting", {})
    section_prompts = build_section_formatting_prompts(template_content, style_formatting)
    section_block = ""
    if section_prompts:
        section_block = section_prompts + "\n\n"

    # Optional: OCR text from template pages (Tesseract) for formatting/structure reference
    ocr_texts = template_page_ocr_texts if template_page_ocr_texts is not None else (style_schema.get("template_page_ocr_texts") or [])
    ocr_block = ""
    if ocr_texts and any(t.strip() for t in ocr_texts):
        lines = [f"Page {i + 1} (OCR):\n{t}" for i, t in enumerate(ocr_texts) if t.strip()]
        if lines:
            ocr_block = "OCR text extracted from template pages (use for layout/structure reference):\n\n" + "\n\n".join(lines) + "\n\n"

    user_text = f"""{template_section}{section_block}{ocr_block}Extracted style guide (use these exact style names as block_type):

{style_guide}
{line_note}

---

Raw text to format. Match the template structure above: use the same styles for titles, section headings, body paragraphs, and lists as in the template. Insert page_break where the template starts a new section on a new page. Output plain text only. Output a JSON array of {{"block_type": "<style name or line/signature_line/page_break>", "text": "<content>"}}.

---
{text}
---"""

    # Build user message: when template page images are passed, send them first as the primary formatting reference
    page_images = template_page_images if template_page_images is not None else (style_schema.get("template_page_images") or [])
    if page_images:
        vision_instruction = (
            "Formatting must follow the uploaded template document. The following images are each page of that template (Page 1, Page 2, ...). "
            "Use these images as your primary reference for how to format the raw text: match the layout, spacing, indentation, headings, captions, "
            "section structure, and placement of content on the page. Assign block_type and segment the raw text so the output document matches "
            "the visual structure of these template pages. Then use the style guide and raw text below.\n\n"
        )
        content = [{"type": "text", "text": vision_instruction + "Template pages (use these for formatting reference):\n\n"}]
        for i, b64 in enumerate(page_images):
            content.append({"type": "text", "text": f"--- Page {i + 1} ---\n"})
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        content.append({"type": "text", "text": "\n\n" + user_text})
    else:
        content = user_text

    # Prefer Azure OpenAI if endpoint and key are set
    azure_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_KEY")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")

    if azure_key and azure_endpoint:
        if not AzureOpenAI:
            raise RuntimeError("Azure OpenAI requested but openai package may be too old. pip install openai>=1.0.0")
        client = AzureOpenAI(
            api_key=azure_key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
            azure_endpoint=azure_endpoint.rstrip("/"),
        )
        model = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("FORMATTER_LLM_MODEL", "gpt-4o-mini")
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "Set OPENAI_API_KEY for OpenAI, or AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT for Azure OpenAI"
            )
        client = OpenAI(api_key=api_key)
        model = os.environ.get("FORMATTER_LLM_MODEL", "gpt-4o-mini")

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ],
        temperature=0.1,
        max_tokens=16384,
    )
    raw = resp.choices[0].message.content.strip()
    # Strip markdown code fence if present
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    # Remove unescaped control characters inside JSON strings (LLM sometimes emits literal newlines/tabs)
    raw = _sanitize_json_control_chars(raw)
    data = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        pass
    if data is None:
        raw_fallback = re.sub(r"[\x00-\x1f]", " ", raw)
        try:
            data = json.loads(raw_fallback)
        except json.JSONDecodeError:
            pass
    if data is None:
        # "Expecting property name" often from unescaped " inside a string or trailing comma
        raw_fallback = re.sub(r"[\x00-\x1f]", " ", raw)
        for repaired in (
            _repair_trailing_commas_json(raw_fallback),
            _repair_unescaped_quotes_in_json(raw_fallback),
            _repair_unescaped_quotes_in_json(_repair_trailing_commas_json(raw_fallback)),
        ):
            try:
                data = json.loads(repaired)
                break
            except json.JSONDecodeError:
                continue
    if data is None:
        raw_fallback = re.sub(r"[\x00-\x1f]", " ", raw)
        data = _recover_truncated_blocks_json(raw_fallback)
        if data is None:
            data = _recover_truncated_blocks_json(raw)
        if data is None:
            raise ValueError(
                "Formatting LLM returned invalid JSON (e.g. unescaped quotes in text or truncated response)."
            )
    out = []
    for item in data:
        bt = (item.get("block_type") or "paragraph").strip()
        if not bt:
            bt = "paragraph"
        # Accept any block_type: template style name or logical type (heading, paragraph, line, etc.)
        out.append((bt, item.get("text", "").strip()))
    return out


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


def _call_openai_slot_fill(text: str, style_schema: dict) -> list[str]:
    """Call LLM to fill N slots from template_structure. Returns list of N text strings."""
    template_structure = style_schema.get("template_structure") or []
    if not template_structure:
        return []
    if not OpenAI and not AzureOpenAI:
        raise RuntimeError("openai package not installed. pip install openai")

    N = len(template_structure)
    block_descriptions = []
    section_ranges = []  # list of (section_type, start, end)
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

Raw text:
---
{text}
---"""

    azure_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_KEY")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if azure_key and azure_endpoint:
        if not AzureOpenAI:
            raise RuntimeError("Azure OpenAI requested but openai package may be too old. pip install openai>=1.0.0")
        client = AzureOpenAI(
            api_key=azure_key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
            azure_endpoint=azure_endpoint.rstrip("/"),
        )
        model = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("FORMATTER_LLM_MODEL", "gpt-4o-mini")
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "Set OPENAI_API_KEY for OpenAI, or AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT for Azure OpenAI"
            )
        client = OpenAI(api_key=api_key)
        model = os.environ.get("FORMATTER_LLM_MODEL", "gpt-4o-mini")

    # Allow long output so slot-fill JSON is not truncated (model cap e.g. 16384)
    max_tokens = 16384
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SLOT_FILL_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.0,
        max_tokens=max_tokens,
    )
    raw = resp.choices[0].message.content.strip()
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
            # Try scanner (handles unescaped quotes / malformed strings)
            extracted = _extract_text_values_from_json_array(raw_fallback, N)
            if extracted is not None:
                return extracted
            extracted = _extract_text_values_from_json_array(raw, N)
            if extracted is not None:
                return extracted
            # Recover from truncated JSON (e.g. "Expecting value" at column 59k): close array and parse
            data = _recover_truncated_slot_json(raw_fallback, N)
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


# Step 5b: Format one section using its formatting instruction (section-by-section path)
SECTION_FORMAT_SYSTEM = """You apply a formatting instruction to one section of text. Your job is to split the section text into blocks and assign each block the correct block_type (style name or line/signature_line/page_break) as specified in the instruction.

Rules:
- Do not change the wording of the text. Only segment and assign block_type.
- Use the exact style names from the formatting instruction (e.g. Heading 1, Normal, List Paragraph).
- For separator lines use block_type "line"; for signature underlines use "signature_line"; for section underlines use "section_underline" with empty text.
- Output a JSON array only. Each element: {"block_type": "<style name or line/signature_line/page_break>", "text": "<content>"}."""


def format_section_with_instruction(
    section_text: str,
    section_formatting_instruction: str,
    style_schema: dict,
) -> list[tuple[str, str]]:
    """Apply the formatting instruction to this section's text; return list of (block_type, text) for this section only."""
    if not section_text or not section_text.strip():
        return []
    style_guide = (style_schema.get("style_guide") or style_schema.get("style_guide_markdown") or "").strip()
    if not style_guide:
        style_list = style_schema.get("paragraph_style_names", []) or list(style_schema.get("style_map", {}).values())
        style_guide = "Style names: " + ", ".join(style_list)

    user_content = f"""Formatting instruction for this section (apply exactly):
---
{section_formatting_instruction or "Use Normal for all."}
---

Style names to use as block_type: {style_guide}

Section text to format (segment into blocks and assign block_type per the instruction above):
---
{section_text.strip()}
---

Output a JSON array only: [{{"block_type": "...", "text": "..."}}, ...]."""

    if not OpenAI and not AzureOpenAI:
        raise RuntimeError("openai package not installed. pip install openai")

    azure_key = os.environ.get("AZURE_OPENAI_API_KEY") or os.environ.get("AZURE_OPENAI_KEY")
    azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if azure_key and azure_endpoint and AzureOpenAI:
        client = AzureOpenAI(
            api_key=azure_key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-15-preview"),
            azure_endpoint=azure_endpoint.rstrip("/"),
        )
        model = os.environ.get("AZURE_OPENAI_DEPLOYMENT") or os.environ.get("FORMATTER_LLM_MODEL", "gpt-4o-mini")
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("Set OPENAI_API_KEY or AZURE_OPENAI_* for formatting")
        client = OpenAI(api_key=api_key)
        model = os.environ.get("FORMATTER_LLM_MODEL", "gpt-4o-mini")

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SECTION_FORMAT_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.1,
        max_tokens=4096,
    )
    raw = resp.choices[0].message.content.strip()
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
            data = _recover_truncated_blocks_json(raw_fallback) or _recover_truncated_blocks_json(raw)
        if data is None:
            raise ValueError("Formatting LLM returned invalid JSON for section")
    out = []
    for item in data:
        bt = (item.get("block_type") or "paragraph").strip() or "paragraph"
        out.append((bt, (item.get("text") or "").strip()))
    return out


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
    template_structure = style_schema.get("template_structure") if use_slot_fill else None
    if template_structure:
        slot_texts = _call_openai_slot_fill(text, style_schema)
        # Return (style, text) per slot so formatter can use exact template structure
        return [
            (template_structure[i].get("style", "Normal"), slot_texts[i] if i < len(slot_texts) else "")
            for i in range(len(template_structure))
        ]
    return _call_openai(
        text,
        style_schema,
        template_page_images=template_page_images,
        template_page_ocr_texts=template_page_ocr_texts,
    )
