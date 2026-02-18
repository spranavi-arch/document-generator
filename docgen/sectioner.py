"""
Divide two uploaded documents into logical sections (name and purpose only).
Extraction is done separately in chunked calls so the full document content is never truncated.
"""
from docgen.llm_client import LLMClient
from docgen.prompts import build_sectioning_prompt
from docgen.utils import clean_text, extract_json_from_llm

llm = LLMClient()

SECTION_KEYS = ("sections", "Sections", "items", "results", "structure", "outline")


def _find_sections_list(data) -> list | None:
    if isinstance(data, list) and data:
        return data
    if not isinstance(data, dict):
        return None
    for k in SECTION_KEYS:
        v = data.get(k)
        if isinstance(v, list) and v:
            return v
    for v in data.values():
        if isinstance(v, list) and v:
            return v
    return None


def _section_item_to_dict(item) -> dict | None:
    if isinstance(item, dict):
        name = (
            item.get("name")
            or item.get("Name")
            or item.get("title")
            or item.get("section")
            or item.get("heading")
        )
        if name:
            purpose = (
                item.get("purpose")
                or item.get("Purpose")
                or item.get("description")
                or ""
            )
            return {"name": str(name).strip(), "purpose": str(purpose).strip()}
    if isinstance(item, str) and item.strip():
        return {"name": item.strip(), "purpose": ""}
    return None


def divide_into_sections(doc1: str, doc2: str) -> dict:
    """
    Returns blueprint: { "sections": [ {"id": 1, "name": "...", "purpose": "..."}, ... ] }.
    Section text is extracted separately so nothing is truncated.
    """
    doc1 = clean_text(doc1)
    doc2 = clean_text(doc2)

    prompt = build_sectioning_prompt(doc1, doc2)
    response = llm.generate(
        prompt,
        json_mode=True,
        max_tokens=8192,
        temperature=0.1,
    )
    data = extract_json_from_llm(response)
    raw_list = _find_sections_list(data)
    if not raw_list:
        raise ValueError("LLM did not return a sections list.")

    sections = []
    for item in raw_list:
        s = _section_item_to_dict(item)
        if s and s["name"]:
            sections.append(s)

    if len(sections) < 5:
        raise ValueError(
            f"Only {len(sections)} sections were identified. Need at least 5 (8–18 recommended depending on document type). "
            "Please try again or use documents with more distinct parts."
        )

    return {
        "sections": [
            {"id": i + 1, "name": s["name"], "purpose": s["purpose"]}
            for i, s in enumerate(sections)
        ]
    }
