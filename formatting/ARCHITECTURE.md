# Formatter architecture (production-grade)

## Recommended: Fixed template + placeholders

For each document type with **one fixed layout** (fixed caption, divider, signature block):

1. **Create the template** in Word with placeholders, e.g.  
   `{{PLAINTIFF}}`, `{{DEFENDANT}}`, `{{INDEX_NO}}`, `{{DATE}}`, `{{ATTORNEY_NAME}}`
2. **Replace placeholders programmatically** — no geometry cloning, no style extraction, no paragraph rebuilding.
3. **Save.**

Layout, dividers, tab stops, indents, and court formatting stay **exact**. You only swap text.

```
Analyzer → content JSON (plaintiff, defendant, index_no, …)
    ↓
Formatter → load fixed template → replace_placeholders() → (optional: insert dynamic sections) → save
```

**API:** `utils/placeholder_docx.py`

- `replace_placeholders(doc, replacements)` — in-place replacement in paragraphs and table cells.
- `generate_from_template(template_path, replacements, output_path)` — load, replace, optionally save.

Use this when: you control the template, structure is known, and legal formatting must be pixel-stable. This is how enterprise legal systems work: preserve template structure, inject data.

---

## Alternative: Arbitrary template (extraction + LLM)

When firms upload **arbitrary** templates and you don’t know structure in advance:

```
Template DOCX → Style + structure extraction → Structure blueprint
       → LLM slot fill / segment → Renderer injects text → Word engine handles formatting
```

**Rule:** The renderer never invents formatting — it only fills the template skeleton.

Use this when: you allow arbitrary templates, must dynamically reconstruct layout, or don’t know structure in advance. More fragile and harder to keep pixel-perfect than placeholder replacement.

---

## Implemented (extraction path)

### Upgrade 1 — Style-only injection (critical)
- **No manual formatting.** When `template_structure` is present we assign `paragraph.style = template_style` and add text only.
- **Clone styles utility:** `clone_styles(src_doc, dst_doc)` in `utils/style_extractor.py`.
- Word handles indentation, numbering, spacing from the template’s style definitions.

### Upgrade 2 — No fake numbering
- Leading "1. " etc. from LLM output is stripped; numbering comes from the template’s list style.

### Upgrade 3 — Section replication
- Template section preserved; no margin override after `clear_document_body(doc)`.

### Upgrade 4 — Preserve blank paragraphs
- `remove_trailing_empty_and_noise(doc)` skipped when slot-fill was used.

### Upgrade 5 — Structure-driven renderer
- Slot-fill only; LLM fills slots; renderer injects text into template styles and block kinds.

---

## TODO (extraction path, if kept)

- Real numbering cloning (abstract numbering from template XML).
- Caption table replication (clone table structure, fill cells).
- Section replication when building from a new doc (clone `sectPr`).

---

## File roles

| File | Role |
|------|------|
| `utils/placeholder_docx.py` | **Fixed templates:** `replace_placeholders()`, `generate_from_template()` — open, replace, save. |
| `utils/style_extractor.py` | Extract styles, template structure, line samples; `clone_styles()` (arbitrary-template path). |
| `utils/formatter.py` | `inject_blocks()`, `clear_document_body()` (arbitrary-template path). |
| `utils/llm_formatter.py` | Slot-fill / segment: LLM maps raw text to template slots or blocks. |
| `backend.py` | Current flow: extract → LLM → inject. For fixed templates, call `generate_from_template()` with Analyzer output instead. |
