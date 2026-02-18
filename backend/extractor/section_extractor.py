from llm.client import LLMClient
from utils.text_utils import extract_json_from_llm

llm = LLMClient()

# Max document length per request (response is one section only, so we can send more doc text)
_EXTRACT_DOC_CHARS = 12000
_EXTRACT_MAX_TOKENS = 4096


def extract_one_section(doc: str, section_name: str) -> str:
    """Ask LLM to extract a single section; returns extracted text or ''."""
    chunk = doc[:_EXTRACT_DOC_CHARS]
    prompt = f"""Extract from the document below ONLY the full text of the section titled exactly: "{section_name}".

Rules:
- Return a JSON object with exactly one key: "{section_name}". The value must be the extracted section text.
- If that section is not found, use: {{"{section_name}": ""}}
- Use double quotes. Escape newlines in the value as \\n. No other text—only the JSON object.

Document:
{chunk}
"""
    response = llm.generate(prompt, max_tokens=_EXTRACT_MAX_TOKENS, json_mode=True)
    try:
        data = extract_json_from_llm(response)
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    # Get value by exact or case-insensitive key
    val = data.get(section_name)
    if val is None:
        for k, v in data.items():
            if k and str(k).strip().lower() == section_name.lower():
                val = v
                break
    if val is None:
        return ""
    return (val if isinstance(val, str) else str(val)).strip()


class SectionExtractor:

    def extract(self, doc, blueprint, on_section=None):
        """
        on_section: optional callback(section_name, index_0based, total) for progress.
        """
        sections = blueprint.get("sections", [])
        section_names = [s.get("name", "") for s in sections if s.get("name")]
        if not section_names:
            return {}

        result = {}
        total = len(section_names)
        for i, name in enumerate(section_names):
            if not name:
                continue
            if callable(on_section):
                on_section(name, i, total)
            text = extract_one_section(doc, name)
            result[name] = text
        return result
