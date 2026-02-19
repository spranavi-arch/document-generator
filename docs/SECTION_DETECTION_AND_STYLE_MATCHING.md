# Section Detection and Style Matching (Legal Layout Intelligence)

This describes the **rule-based section detection** and **style matching** pipeline that turns flat LLM text into typed blocks so the formatter can apply court-grade layout (captions, headings, allegations, WHEREFORE, signature blocks).

## Problem

LLM output is a single flat text stream with **implicit** structure (court caption, parties, causes of action, allegations, WHEREFORE, verification). The formatter needs **explicit** block types to know what to center, bold, indent, and how much spacing to apply.

## Pipeline

```
Raw LLM text
    → section_detector.detect_blocks()   [rule-based, no LLM]
    → list of (ontology_type, text)
    → style_matcher.blocks_to_formatter_blocks(..., style_map)
    → list of (template_style_name | "line" | "signature_line", text)
    → formatter.inject_blocks(doc, blocks, style_map, ...)
    → Styled DOCX
```

## Modules

### 1. `formatting/utils/legal_block_ontology.py`

- **Ontology block types**: `court_header`, `county_line`, `caption_separator`, `caption_party`, `caption_role`, `versus_line`, `doc_title`, `notice_to_line`, `section_heading`, `cause_of_action_heading`, `cause_of_action_title`, `body_paragraph`, `legal_allegation`, `numbered_paragraph`, `wherefore_clause`, `signature_line`, `signature_block`, `verification_heading`, `verification_body`, `summons_body`, `line`, `empty`.
- **`ONTOLOGY_TO_STYLE_MAP_KEY`**: maps each ontology type to a **style_map key** (`heading`, `section_header`, `paragraph`, `numbered`, `wherefore`) so the style matcher can resolve to a template style name.

### 2. `formatting/utils/section_detector.py`

- **`split_into_paragraphs(raw_text)`**: splits on blank lines; separator lines (e.g. `----X`) are kept as single paragraphs.
- **`classify_paragraph(text)`**: returns an ontology block type using heuristics:
  - Court caption: ALL CAPS + "COURT"
  - County line: ALL CAPS + "COUNTY" / "DISTRICT"
  - `-against-` → `versus_line`
  - WHEREFORE at start → `wherefore_clause`
  - Plaintiff/Defendant/Claimant → `caption_party` / `caption_role`
  - Short ALL CAPS (SUMMONS, NOTICE OF CLAIM, etc.) → `doc_title`
  - "TO:" / "TO THE " → `notice_to_line`
  - "AS AND FOR" / "CAUSE OF ACTION" → `cause_of_action_heading`
  - "VERIFICATION" / "AFFIDAVIT" → `verification_heading`
  - "That on...", "By reason of...", etc. → `legal_allegation`
  - "1. ", "2. " → `numbered_paragraph`
  - Underscore line / ESQ / Attorneys for → `signature_line` / `signature_block`
  - Default → `body_paragraph`
- **`detect_blocks(raw_text)`**: returns `list[tuple[str, str]]` of `(ontology_type, text)`.

### 3. `formatting/utils/style_matcher.py`

- **`resolve_block_style(ontology_type, style_map)`**: returns a **template style name** (e.g. "Heading 1", "Normal") or the literal `"line"` / `"signature_line"` for formatter special handling.
- **`blocks_to_formatter_blocks(blocks, style_map)`**: converts `(ontology_type, text)` → `(block_type, text)` for `inject_blocks`.

### 4. Backend integration (`formatting/backend.py`)

- **`process_document(generated_text, template_file, use_section_detector=False)`**:
  - **`use_section_detector=True`**: skip LLM and template images; run `detect_blocks` → `blocks_to_formatter_blocks` → `inject_blocks`.
  - **`use_section_detector=False`**: call `format_text_with_llm` as before; if it fails or returns no blocks, **fallback** to the same rule-based path.

### 5. Streamlit UI (`formatting/app.py`)

- Checkbox **"Use rule-based formatting only (no LLM)"**: when checked, formatting uses section detection only (no API key, fast).
- Env **`USE_SECTION_DETECTOR=1`** (or `true`/`yes`) sets the default value of that checkbox.

## Style mapping (ontology → template)

| Ontology type           | style_map key   | Typical template style |
|-------------------------|-----------------|-------------------------|
| court_header, county_line, caption_* | heading        | Heading 1               |
| doc_title, section_heading, cause_*  | section_header | Heading 2               |
| notice_to_line          | section_header  | Heading 2               |
| body_paragraph, summons_body, verification_body | paragraph | Normal        |
| legal_allegation, numbered_paragraph | numbered       | List Number             |
| wherefore_*             | wherefore       | Heading 2 or custom     |
| signature_*, firm_block_line | paragraph   | Normal                  |
| line                    | (special)       | "line" → separator line |
| signature_line          | (special)       | "signature_line" → underline |

## Extending

- **New block type**: add constant in `legal_block_ontology.py`, add entry in `ONTOLOGY_TO_STYLE_MAP_KEY`, then add detection rule in `section_detector.classify_paragraph()`.
- **Template-specific styles**: the style_map is built from the uploaded DOCX by `style_extractor.build_style_map_from_doc()`; ontology types are mapped to that map’s keys, so any template with Heading 1 / Normal / List Number etc. will receive the correct style.
- **LLM fallback for low confidence**: you can add a second pass that calls the LLM only for paragraphs where the rule-based classifier is uncertain (e.g. short or ambiguous lines), then merge with rule-based results.
