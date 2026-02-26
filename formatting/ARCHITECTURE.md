# Formatter architecture — template workflow only

## Current approach: Sample → template → fill with JSON

For each document type with **one fixed layout** (fixed caption, divider, signature block):

1. **Build template from sample:** `utils/template_builder.sample_to_template()` — extract run map, detect fields by heuristics, insert `{{PLACEHOLDER}}` run-safely, save `template.docx` and `schema.json`.
2. **Fill template with JSON:** `utils/template_filler.fill_template()` — validate JSON against schema, replace scalars via `placeholder_docx.replace_placeholders()`, merge block placeholders (e.g. `{{CAUSE_OF_ACTION_1_PARAS__BLOCK}}`), run normalization.
3. **No geometry cloning, no style extraction, no document rebuild.** Layout stays identical to the sample; only text is replaced or blocks expanded.

```
sample.docx → template_builder → template.docx + schema.json
       → LLM (optional) returns JSON matching schema
       → template_filler.fill_template() → filled_output.docx
```

---

## Two-sample: deterministic diff + LLM classification

When **two** samples are provided (primary + secondary), the system uses **`utils/two_sample_blueprint`**:

1. **Extract units:** Both DOCX to `TextUnit` list in document order (`extract_units(doc)` via `iter_body_blocks`). Same extraction for both; stable `unit_id` (e.g. `u:0012`).
2. **Structural diff:** Align segments by position; for each pair where text differs, compute word-level diff to get differing spans (in primary’s coordinates). `diff_documents(doc_primary, doc_secondary)`.
3. **Classify spans:** Each differing span is sent to the LLM with context (or a heuristic fallback): “Sample 1: X, Sample 2: Y, context: … → assign one semantic field name” (e.g. `PLAINTIFF_NAME`, `DEFENDANT_NAME`, `ACCIDENT_DATE`). Controlled vocabulary + optional new `UPPER_SNAKE_CASE` names.
4. **Classify (constrained LLM or heuristic):** One compact JSON payload with all diffs; LLM returns `mappings`: anchor to `field_name`, `field_type`, `confidence`, `notes`. If LLM is unavailable, `_heuristic_classify()` uses context. **Apply placeholders:** replace from end to start per unit; save primary as `template.docx`, build schema, merge with secondary keys for validation_info.
5. Hardening: `filter_boilerplate_diffs()` drops long, low-similarity spans (structure drift).

---

## File roles

| File | Role |
|------|------|
| `utils/placeholder_docx.py` | Run-safe scalar replacement: `replace_placeholders()`, `generate_from_template()`. |
| `utils/template_builder.py` | Sample → template (single-sample): run map, heuristics, run-safe placeholder insertion, schema. Entry: `sample_to_template()`. |
| `utils/two_sample_blueprint.py` | Two-sample pipeline: `TextUnit`/`SpanDiff`, `extract_units()`, `align_units()`, `char_span_diffs()`, `collect_span_diffs()`, LLM payload + `parse_llm_mappings()`, `apply_placeholders_to_docx()`, `infer_placeholders_from_two_docx()`. |
| `utils/structural_diff.py` | Legacy/alt: `doc_to_segments()`, `diff_documents()` (paragraph-level diff). |
| `utils/template_filler.py` | Load schema, validate JSON, scalar + block merge, run normalization. Entry: `fill_template()`. Prompt helper: `build_template_fill_prompt()`, `parse_llm_json_response()`. |
| `utils/style_extractor.py` | `iter_body_blocks(doc)`, template structure extraction (used by template_builder and two_sample_blueprint). |
| `utils/formatter.py` | Minimal: `force_legal_run_format()` / `force_legal_run_format_document()` only (used by template_filler after merge). |
| `utils/llm_formatter.py` | Stub; previous block-segmentation path removed. Use `template_filler` for schema + case facts → JSON. |
| `backend.py` | Template workflow: `build_template_from_sample(primary, secondary=None)` uses `infer_placeholders_from_two_docx` when secondary provided; `fill_template_from_json()`, `get_document_preview_text()`. |
