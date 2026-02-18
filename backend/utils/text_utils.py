import json
import re


def _try_parse(s: str):
    """Try json.loads; optionally fix trailing commas, escape newlines in strings, and retry."""
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    # Fix trailing commas (common in LLM output)
    fixed = re.sub(r",\s*}", "}", re.sub(r",\s*]", "]", s))
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    # Escape raw newlines/tabs inside double-quoted strings (LLMs often forget to escape)
    fixed = _escape_newlines_in_json_strings(s)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    fixed = re.sub(r",\s*}", "}", re.sub(r",\s*]", "]", fixed))
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return None


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


def extract_json_from_llm(response: str):
    """Parse JSON from LLM response, stripping markdown code blocks and extra text."""
    if not response or not response.strip():
        raise ValueError("LLM returned empty response. Check API key, quota, and model.")
    text = response.strip()
    # Remove markdown code fences (```json ... ``` or ``` ... ```)
    if "```" in text:
        start = text.find("```")
        if text[start:].startswith("```json"):
            start += 7
        else:
            start = text.find("\n", start) + 1 if "\n" in text[start:] else start + 3
        end = text.rfind("```")
        if end > start:
            text = text[start:end].strip()
    if not text:
        raise ValueError("LLM returned no JSON. It may have returned an error message instead.")
    # Strip leading text before first { or [ (e.g. "Here is the JSON:" or explanatory sentence)
    for start_char in ("{", "["):
        pos = text.find(start_char)
        if pos > 0:
            text = text[pos:].strip()
            break
    parsed = _try_parse(text)
    if parsed is not None:
        return parsed
    # Find first complete { ... } or [ ... ] (brace-matching, ignoring quotes naively can fail)
    for start_char, end_char in (("{", "}"), ("[", "]")):
        idx = text.find(start_char)
        if idx == -1:
            continue
        depth = 0
        in_str = None
        escape = False
        for i in range(idx, len(text)):
            c = text[i]
            if escape:
                escape = False
                continue
            if c == "\\" and in_str:
                escape = True
                continue
            if in_str:
                if c == in_str:
                    in_str = None
                continue
            if c in ('"', "'"):
                in_str = c
                continue
            if c == start_char:
                depth += 1
            elif c == end_char:
                depth -= 1
                if depth == 0:
                    chunk = text[idx : i + 1]
                    parsed = _try_parse(chunk)
                    if parsed is not None:
                        return parsed
                    break
        else:
            continue
        break
    snippet = (text[:400] + "...") if len(text) > 400 else text
    raise ValueError(
        "LLM did not return valid JSON. Try shorter documents or check the model response. "
        f"Raw snippet: {snippet!r}"
    )


def clean_text(text):

    text = text.replace("\t", " ")
    text = re.sub(" +", " ", text)
    text = re.sub("\n{3,}", "\n\n", text)

    return text.strip()
