"""
Map ontology block types (from section_detector) to template style names for inject_blocks.
Uses legal_block_ontology.ONTOLOGY_TO_STYLE_MAP_KEY and the schema's style_map so
court_header -> heading style, legal_allegation -> numbered style, etc.
"""

from utils.legal_block_ontology import (
    LINE,
    ONTOLOGY_TO_STYLE_MAP_KEY,
    SIGNATURE_LINE,
)


def resolve_block_style(ontology_type: str, style_map: dict) -> str:
    """
    Resolve an ontology block type to a template style name for use as block_type in inject_blocks.
    - For LINE and SIGNATURE_LINE we return the literal "line" / "signature_line" so the formatter
      applies special handling (separator line, signature underline).
    - For all other types we return the template style name from style_map using the ontology fallback chain.
    """
    if not ontology_type or ontology_type not in ONTOLOGY_TO_STYLE_MAP_KEY:
        return (style_map or {}).get("paragraph") or "Normal"
    if ontology_type == LINE:
        return "line"
    if ontology_type == SIGNATURE_LINE:
        return "signature_line"
    style_map_key = ONTOLOGY_TO_STYLE_MAP_KEY[ontology_type]
    return (style_map or {}).get(style_map_key) or (style_map or {}).get("paragraph") or "Normal"


def blocks_to_formatter_blocks(blocks: list[tuple[str, str]], style_map: dict) -> list[tuple[str, str]]:
    """
    Convert list of (ontology_type, text) from section_detector.detect_blocks into
    list of (block_type, text) expected by formatter.inject_blocks (block_type = template style name or "line"/"signature_line").
    """
    out = []
    for ontology_type, text in blocks:
        block_type = resolve_block_style(ontology_type, style_map)
        out.append((block_type, text or ""))
    return out
