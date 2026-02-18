"""
Assemble generated sections in blueprint order into final draft.
Renumber numbered paragraphs (1., 2., 3., ...) continuously across the whole document.
"""
import re


def renumber_numbered_paragraphs(text: str) -> str:
    """Renumber lines that start with 'N.' or 'N)' so they run 1, 2, 3, ... across the document."""
    if not text or not text.strip():
        return text
    lines = text.split("\n")
    result = []
    num = 1
    # Match line start: optional whitespace, digits, then . or )
    pattern = re.compile(r"^(\s*)(\d+)([.)])(\s*)(.*)$")
    for line in lines:
        m = pattern.match(line)
        if m:
            prefix, _old_num, punct, space, rest = m.groups()
            result.append(f"{prefix}{num}{punct}{space}{rest}")
            num += 1
        else:
            result.append(line)
    return "\n".join(result)


def _strip_leading_section_title(text: str, section_name: str) -> str:
    if not text or not section_name:
        return text
    lines = text.strip().splitlines()
    if not lines:
        return text
    first = lines[0].strip().lstrip(".#0123456789) ").strip().strip("*_")
    name_norm = section_name.strip().strip("*_")
    if first.lower() == name_norm.lower():
        rest = "\n".join(lines[1:]).strip()
        return rest if rest else text
    return text


def assemble(blueprint: dict, sections: dict | list) -> str:
    """
    Join sections in blueprint order; then renumber list items continuously.
    sections: either a dict (section name -> text) or a list of texts in the same order as blueprint['sections'].
    Use a list when section names can repeat (avoids same content appearing multiple times).
    """
    section_list = blueprint.get("sections", [])
    parts = []
    for i, s in enumerate(section_list):
        name = s.get("name", "")
        if isinstance(sections, list):
            text = (sections[i] if i < len(sections) else "").strip()
        else:
            text = (sections.get(name) or "").strip()
        text = _strip_leading_section_title(text, name)
        if text:
            parts.append(text)
    draft = "\n\n".join(parts)
    return renumber_numbered_paragraphs(draft)
