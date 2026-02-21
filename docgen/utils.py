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
