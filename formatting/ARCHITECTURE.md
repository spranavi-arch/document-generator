# Formatter architecture (production-grade)

## Pipeline

```
Template DOCX → Style + structure extraction → Structure blueprint
       → LLM slot fill → Renderer injects text only → Word engine handles formatting
```

**Rule:** The renderer never invents formatting — it only fills the template skeleton.

---

## Implemented

### Upgrade 1 — Style-only injection (critical)
- **No manual formatting.** When `template_structure` is present we assign `paragraph.style = template_style` and add text only. No `_apply_paragraph_format()` or `_apply_run_format()`.
- **Clone styles utility:** `clone_styles(src_doc, dst_doc)` in `utils/style_extractor.py` copies paragraph style definitions from template to a destination doc (for building new docs from scratch).
- Word handles indentation, numbering, spacing from the template’s style definitions.

### Upgrade 2 — No fake numbering
- **Removed** prepending `"1. ", "2. "` in code. List/numbered paragraphs get the template’s list style; numbering comes from the style (and, when implemented, from cloned numbering definitions).
- Leading "1. " etc. from LLM output is stripped so the paragraph contains only the allegation text.

### Upgrade 3 — Section replication
- **Template section preserved.** We no longer override section margins after `clear_document_body(doc)`. Page breaks, margins, and columns come from the template.

### Upgrade 4 — Preserve blank paragraphs
- **No aggressive trim when using template.** `remove_trailing_empty_and_noise(doc)` is skipped when slot-fill was used (`template_structure` is not None) so legal spacing and blank paragraphs are preserved.

### Upgrade 5 — Structure-driven renderer
- **Slot-fill only.** Template structure slots → fill text → preserve structure. LLM fills slots; renderer only injects text into the template’s styles and block kinds (line, signature_line, section_underline, paragraph).

---

## TODO (future)

### Real numbering cloning (Upgrade 2 full)
- Copy **abstract numbering definitions** from template XML: `doc.part.numbering_part` (and related parts).
- Attach numbering to paragraphs so allegations align exactly with the template.
- Until then, numbering is driven by the template’s list styles in the same document (we edit the template in place).

### Caption table replication (Upgrade 3)
- **Detect caption region** in the template (e.g. table with left/right cells).
- **Clone table structure** and insert text into cells instead of flat paragraphs.
- `template_structure` already has `table_id`, `row`, `col` for in-table blocks; the renderer still outputs paragraphs only. Next step: group slots by table and emit tables.

### Section replication when building a new doc (Upgrade 4 full)
- If we ever build the output from a **new** document instead of clearing the template, clone **section properties XML** (`sectPr`) so page breaks, header/footer spacing, and columns match the template.

---

## File roles

| File | Role |
|------|------|
| `utils/style_extractor.py` | Extract styles, template structure, line samples; `clone_styles()` |
| `utils/formatter.py` | `inject_blocks()`: structure-driven, style-only; `clear_document_body()` |
| `utils/llm_formatter.py` | Slot-fill: LLM maps raw text to template slots |
| `backend.py` | Load template → extract → LLM → inject (no margin override); skip trailing trim when template used |
