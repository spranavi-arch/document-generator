# Document generation architecture

## Two flows

### 1. Template flow (placeholders)

DOCX → insert placeholders → inject JSON → export. See Phases below.

### 2. Layout-Aware flow (dynamic mapping, no hardcoded placeholders)

- **Sample DOCX** defines layout (geometry, styles, regions).
- **Frontend text** defines content (raw paste or narrative).
- **DocumentStructureExtractor** detects regions by markers: caption (-against-), allegation region (first numbered para after "AS AND FOR A FIRST CAUSE OF ACTION"), signature block (first "ESQ"), footer ("NOTICE OF ENTRY" / "NOTICE OF SETTLEMENT").
- **FrontendTextExtractor** (LLM): raw text → JSON only (plaintiff, defendant, allegations, etc.).
- **LayoutAwareInjector** maps content into layout: replace plaintiff/defendant by structural position, index/date by label, remove existing allegation paragraphs and insert new ones with same style/numbering. No global text replace; paragraph formatting preserved.

Allegation count adapts automatically from `len(data["allegations"])`.

```
sample.docx (layout) + frontend text → FrontendTextExtractor (LLM) → JSON
       → LayoutAwareInjector (structural inject) → layout_filled_output.docx
```

---

## Flow: DOCX → freeze geometry → placeholders → inject JSON → export

We **open the DOCX directly** and **freeze its geometry**. No reconstruction from pixels or images.

1. Open sample DOCX with python-docx.
2. Insert placeholders (text replacement only; no style/alignment/tab/border changes).
3. Inject JSON data (scalar + block) via TemplateFiller.
4. Export final DOCX.
5. **Optionally:** DOCX → HTML for preview (e.g. `utils/docx_to_html.docx_to_html()`).

**Key point:** Formatting is derived from the **DOCX structure** (paragraph styles, paragraph_format, runs), not from pixels or rendered images. Deterministic and reproducible.

---

## Optional: Style blueprint (deterministic extraction)

If you want a **style blueprint** for documentation or automation, use **`utils/style_blueprint`**:

- Uses **python-docx only** to extract: paragraph styles, paragraph_format (alignment, indents, spacing), tab_stops, border definitions, run styles (bold, italic, font size).
- Produces a **JSON blueprint** like:
  ```json
  {
    "caption_block": {
      "alignment": "CENTER",
      "border_bottom": true,
      "tab_stops": [ { "position_pt": 432, "alignment": "RIGHT" } ],
      "first_line_indent_pt": 0,
      "left_indent_pt": 0
    },
    "Normal": { ... }
  }
  ```
- **Deterministic and reproducible.** No images involved.

---

## Phases (unchanged)

1. **Phase 1 — Convert sample into template:** `utils/sample_to_template.convert_sample_to_template()` — text-only placeholder insertion. Saves `summons_template.docx`.
2. **Phase 2 — Schema:** `utils/schema.py` — SUMMONS_SCHEMA_SPEC, validate_summons_data().
3. **Phase 3 — LLM:** Case facts → JSON only; validate.
4. **Phase 4 — Merge:** `utils/template_filler.TemplateFiller` — fill_scalar / fill_block; export final DOCX.

```
sample.docx → sample_to_template → summons_template.docx
       → LLM(case facts) → JSON → validate_summons_data()
       → TemplateFiller → final_output.docx
       → (optional) docx_to_html → preview
```

---

## File roles

| File | Role |
|------|------|
| `utils/schema.py` | SUMMONS_SCHEMA, SUMMONS_SCHEMA_SPEC, validate_summons_data(). |
| `utils/sample_to_template.py` | Phase 1: convert_sample_to_template() — text-only placeholder insertion. |
| `utils/template_filler.py` | Phase 4: TemplateFiller (fill_scalar, fill_block), fill_template_from_data(), iter_body_blocks(). |
| `utils/placeholder_docx.py` | Run-safe {{KEY}} replacement (replace_marker_across_runs, replace_placeholders). |
| `utils/style_blueprint.py` | Optional: build_style_blueprint(doc) → JSON (alignment, tab_stops, borders, indents; no images). |
| `utils/docx_to_html.py` | Optional: DOCX → HTML for preview. |
| `utils/template_debug.py` | Debug placeholders in template. |
| `utils/document_structure.py` | DocumentStructureExtractor, extract_structure() — caption, allegation region, signature, footer by markers. |
| `utils/frontend_extractor.py` | FrontendTextExtractor — LLM: raw text → JSON only (plaintiff, defendant, allegations, etc.). |
| `utils/layout_injector.py` | LayoutAwareInjector — structural replace (no global replace), preserve format; allegation count adapts. |
| `backend.py` | Template + Layout-Aware: build_template_from_sample, fill_template_from_json, extract_from_frontend_text, inject_into_layout, get_layout_structure(), _call_llm(). |

---

## Removed (previous approach)

- Style extraction from pixels/images, paragraph_format cloning in main flow, geometry reconstruction, Formatting Agent, LLM-based block injection.
- Deleted: `style_extractor.py`, `formatter.py`, `llm_formatter.py`, `section_detector.py`, `style_matcher.py`, `legal_block_ontology.py`, `two_sample_blueprint.py`, `structural_diff.py`, `template_builder.py`.
