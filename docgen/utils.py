"""
JSON parsing and text utilities. Encapsulated in classes (OOP).
"""
import json
import re


class JsonParser:
    """
    Handles parsing JSON from LLM responses: escape newlines, repair truncated JSON,
    strip markdown fences, and extract valid JSON.
    """

    @staticmethod
    def _escape_newlines_in_json_strings(s: str) -> str:
        """Replace raw newlines and tabs inside JSON string values with \\n and \\t."""
        result = []
        i = 0
        in_string = False
        escape = False
        while i < len(s):
            c = s[i]
            if not in_string:
                result.append(c)
                if c == '"':
                    in_string = True
                i += 1
                continue
            if escape:
                result.append(c)
                escape = False
                i += 1
                continue
            if c == "\\":
                result.append(c)
                escape = True
                i += 1
                continue
            if c == '"':
                result.append(c)
                in_string = False
                i += 1
                continue
            if c == "\n":
                result.append("\\n")
                i += 1
                continue
            if c == "\r":
                result.append("\\r")
                i += 1
                continue
            if c == "\t":
                result.append("\\t")
                i += 1
                continue
            result.append(c)
            i += 1
        return "".join(result)

    @staticmethod
    def _repair_truncated_json(s: str) -> str:
        """Attempt to close truncated JSON by appending missing ] and }."""
        s = s.rstrip()
        if not s:
            return s
        open_braces = s.count("{") - s.count("}")
        open_brackets = s.count("[") - s.count("]")
        if open_brackets > 0 or open_braces > 0:
            s += "]" * open_brackets + "}" * open_braces
        return s

    @classmethod
    def _try_parse(cls, s: str):
        """Try json.loads; fix trailing commas, unescaped newlines, and truncated JSON."""
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            pass
        fixed = re.sub(r",\s*}", "}", re.sub(r",\s*]", "]", s))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        fixed = cls._escape_newlines_in_json_strings(s)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        fixed = re.sub(r",\s*}", "}", re.sub(r",\s*]", "]", fixed))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        fixed = cls._repair_truncated_json(s)
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        fixed = cls._repair_truncated_json(cls._escape_newlines_in_json_strings(s))
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            return None

    @classmethod
    def extract_json_from_llm(cls, response: str):
        """Parse JSON from LLM response. Handles markdown fences, trailing commas, unescaped newlines."""
        if not response or not response.strip():
            raise ValueError("LLM returned empty response.")
        text = response.strip()
        if "```" in text:
            start = text.find("```")
            if text[start:].startswith("```json"):
                start += 7
            else:
                start = text.find("\n", start) + 1 if "\n" in text[start:] else start + 3
            end = text.rfind("```")
            if end > start:
                text = text[start:end].strip()
        for start_char in ("{", "["):
            pos = text.find(start_char)
            if pos >= 0:
                text = text[pos:].strip()
                break
        parsed = cls._try_parse(text)
        if parsed is not None:
            return parsed
        raise ValueError("LLM did not return valid JSON.")


# Placeholder pattern: [anything]
_PLACEHOLDER_PATTERN = re.compile(r"\[([^\]]+)\]")


def _build_placeholder_context(field_values: dict) -> str:
    """Build a single context string: case summary first, then all extracted field values."""
    if not field_values:
        return ""
    parts = []
    for key in ("case_summary", "case_summary_or_context", "extra_context"):
        v = field_values.get(key)
        if v is not None and str(v).strip():
            parts.append(f"Case summary / context:\n{str(v).strip()}")
            break
    others = [(k, v) for k, v in field_values.items() if v is not None and str(v).strip()
              and k not in ("case_summary", "case_summary_or_context", "extra_context")]
    if others:
        parts.append("Extracted field values (each value may be a full API/response; use only the exact value needed):")
        for k, v in others:
            parts.append(f"  {k}: {str(v).strip()}")
    return "\n\n".join(parts) if parts else ""


def fill_placeholders_from_context_with_llm(
    draft: str,
    field_values: dict,
    llm_client=None,
) -> str:
    """
    Use the LLM to fill [placeholder] tokens from case summary and all field values.
    The LLM must check every extracted field and use only the exact value (e.g. just
    the name, date, or number) — not the whole field response. Falls back to
    key-based lookup if the LLM call fails.
    """
    if not draft or not field_values:
        return draft
    if not _PLACEHOLDER_PATTERN.search(draft):
        return draft

    context = _build_placeholder_context(field_values)
    if not context.strip():
        return fill_placeholders_from_field_values(draft, field_values)

    prompt = f"""You are filling placeholders in a legal document draft. Use ONLY the context below (case summary and all extracted field values).

RULES:
1. Check ALL field values and the case summary. Match each [placeholder] to the right source (e.g. [plaintiff_name] → the exact name from plaintiff_name or case summary, not a full sentence).
2. Fill with the EXACT VALUE only: for a name use only the name; for a date use only the date; for an amount use only the number; for an address use only the address. Do NOT paste whole sentences or the full field response — extract the precise value that the placeholder asks for.
3. For any attorney-related placeholder (e.g. attorney name, attorney address, signing attorney): if it is unclear which party's attorney is meant, use the plaintiff's attorney details.
4. If the information is not clearly present in the context, leave the placeholder exactly as [placeholder]. Do not invent or guess.
5. Return ONLY the filled draft, no explanation or commentary.

Context:
---
{context}
---

Draft with placeholders to fill:
---
{draft}
---

Return only the filled draft (same structure, [placeholders] replaced with exact values only)."""

    try:
        if llm_client is None:
            from docgen.llm_client import LLMClient
            llm_client = LLMClient()
        filled = llm_client.generate(
            prompt,
            max_tokens=8192,
            temperature=0.0,
        )
        if filled and filled.strip():
            return filled.strip()
    except Exception:
        pass
    return fill_placeholders_from_field_values(draft, field_values)


def fill_placeholders_from_field_values(draft: str, field_values: dict) -> str:
    """
    Find all [placeholder] tokens in the draft and replace them with values from
    field_values when a match is found (exact key, normalized key, or key containing
    the placeholder). Used as fallback when LLM is not used or fails.
    """
    if not draft or not field_values:
        return draft
    seen_placeholders = set()
    result = draft
    for m in _PLACEHOLDER_PATTERN.finditer(draft):
        placeholder = m.group(1).strip()
        if not placeholder or placeholder in seen_placeholders:
            continue
        seen_placeholders.add(placeholder)
        value = _lookup_field_value(placeholder, field_values)
        if value is not None and str(value).strip():
            result = result.replace(f"[{placeholder}]", str(value).strip())
    return result


def _lookup_field_value(placeholder: str, field_values: dict):
    """Try to find a value for placeholder from field_values. Returns value or None."""
    if not placeholder or not field_values:
        return None
    # 1. Exact key (e.g. [plaintiff_name] -> plaintiff_name)
    v = field_values.get(placeholder)
    if v is not None and str(v).strip():
        return v
    # 2. Normalize: lowercase, spaces and common punctuation to underscore
    normalized = re.sub(r"[\s.\-,]+", "_", placeholder.lower()).strip("_")
    if not normalized:
        return None
    v = field_values.get(normalized)
    if v is not None and str(v).strip():
        return v
    # 3. Any key that equals normalized or contains it (e.g. [Date] -> date_of_filing)
    for key, val in field_values.items():
        if not key or val is None or not str(val).strip():
            continue
        key_lower = key.lower()
        if key_lower == normalized or normalized in key_lower or key_lower in normalized:
            return val
    return None


class TextUtils:
    """
    Text cleaning and diff utilities.
    """

    @staticmethod
    def clean_text(text: str) -> str:
        text = text.replace("\t", " ")
        text = re.sub(r" +", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def diff_drafts(
        before: str,
        after: str,
        fromfile: str = "Before validation",
        tofile: str = "After validation",
    ) -> str:
        """
        Return a unified diff between two draft texts so users can see what changed after validation.
        Lines prefixed with '-' were removed, '+' were added.
        """
        import difflib
        before_lines = (before or "").splitlines(keepends=True)
        after_lines = (after or "").splitlines(keepends=True)
        if not before_lines and not after_lines:
            return "(No changes — both drafts empty.)"
        diff = difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
        result = "".join(diff)
        return result if result.strip() else "(No differences — drafts are identical.)"


# Backward-compatible module-level functions
def extract_json_from_llm(response: str):
    return JsonParser.extract_json_from_llm(response)


def clean_text(text: str) -> str:
    return TextUtils.clean_text(text)


def diff_drafts(before: str, after: str, fromfile: str = "Before validation", tofile: str = "After validation") -> str:
    return TextUtils.diff_drafts(before, after, fromfile, tofile)
