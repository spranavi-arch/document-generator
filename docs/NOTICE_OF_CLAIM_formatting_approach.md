# Approach to Achieve NOTICE OF CLAIM–Style Formatting

This doc describes how to get the formatting in your NOTICE OF CLAIM sample (centered bold title, indented caption, justified body, explicit numbering, two-column claimant/attorney block, selective bold) using **CKEditor 5** and the existing **Python DOCX pipeline**.

---

## 1. Two Ways to Produce the Document

| Approach | When to use | How |
|----------|-------------|-----|
| **A. Template + LLM (existing)** | Content is drafted from case summary; you have a DOCX template that already looks like the target. | Upload template → enter text → “Format with LLM” in Streamlit (`formatting/app.py`). The pipeline applies template styles, alignment, numbering, and tabs. Download the formatted DOCX; optionally edit in CKEditor or re-export from CKEditor. |
| **B. CKEditor-first** | Author edits entirely in the browser; no template, or template is only a style reference. | Compose in CKEditor 5 at `/ckeditor` → “Download as DOCX”. The `/ckeditor/api/export-docx` endpoint converts HTML to DOCX using `html_to_docx` (alignment, bold/italic/underline, lists, tables, legal heuristics). |

You can combine both: e.g. format once with template + LLM, then paste the result into CKEditor for tweaks and download again.

---

## 2. Formatting Elements and How to Get Them

### 2.1 Main title: “NOTICE OF CLAIM” (centered, bold, uppercase)

- **In CKEditor:** Type the line, select it, apply **Center** and **Bold** from the toolbar.
- **In DOCX:** `html_to_docx` already treats “NOTICE OF CLAIM” (and similar titles) as center + bold via `_legal_paragraph_format` in `formatting/utils/html_to_docx.py`. So a paragraph that is exactly “NOTICE OF CLAIM” (or similar short all-caps title) will be centered and bold even if you only set alignment in the editor.

### 2.2 Case caption block (“In the Matter of the Claim of:”, Claimant, “-Against-”, Respondent)

- **In CKEditor:** Use a **left indent** (or a block with margin) so the whole block is indented. Use **Center** for “-Against-” only. Add a short horizontal line (e.g. `---` or Insert → Horizontal line) after the respondent if desired.
- **In DOCX:** `html_to_docx` reads `style="text-align: center"` for centered lines. Indentation is not yet inferred from HTML margin/indent; for pixel-perfect indent you can either (1) use a 1-column table with one cell and no visible borders, or (2) extend the HTML parser to map something like `style="margin-left: 1in"` or a class to `paragraph_format.left_indent`.

### 2.3 Recipient line (“TO: CITY OF NEW YORK, 100 Church Street…”)

- **In CKEditor:** Plain paragraph, left-aligned (default).
- **In DOCX:** Renders as left-aligned body; no extra logic needed.

### 2.4 Introductory paragraph (justified, with bold “CITY OF NEW YORK” and “respondent”)

- **In CKEditor:** Type the paragraph; select the phrases to bold and apply **Bold**.
- **In DOCX:** `html_to_docx` preserves `<strong>`/`<b>` as bold runs. Justified alignment: set **Justify** in the editor on that paragraph, or rely on the legal heuristic (long paragraphs are justified by default in `html_to_docx`).

### 2.5 Numbered sections (“1.”, “2.”, “3.”)

- **In CKEditor:** Type the numbers explicitly (e.g. “1. The name and post-office address…”). Do **not** use the editor’s automatic numbered list if you want court-style “1.” “2.” “3.” that must stay in the text.
- **In DOCX:** `html_to_docx` already treats paragraphs that start with “1.” “2.” etc. as numbered and applies hanging indent. So explicit numbering in the text is the right approach and matches “template fidelity” in the LLM pipeline.

### 2.6 Two-column block (attorney left, claimant right)

- **In CKEditor (recommended):** Insert a **Table** with 1 row and 2 columns. Put attorney info in the left cell and claimant info in the right cell. No need for visible borders (you can use a borderless table or rely on “Table Grid” and then adjust in Word if needed).
- **In DOCX:** The CKEditor export endpoint already supports tables; `html_to_docx` converts `<table>` to a Word table. So the two-column layout is preserved.
- **Alternative (template pipeline):** The LLM can output **tab characters** between left and right content; the template formatter applies tab stops so the layout matches the template. For CKEditor-only flow, the table approach is simpler and more reliable.

### 2.7 Remaining body (justified, selective bold)

- **In CKEditor:** Justify paragraphs (toolbar alignment). Bold key terms (e.g. “Personal Injury Action”, “NOVEMBER 2, 2025”, “EAST 51ST STREET”, “defective”) with the Bold button.
- **In DOCX:** Alignment and bold are preserved by `html_to_docx`; long paragraphs get justified by default when no explicit alignment is set.

---

## 3. CKEditor 5 Setup Recommendations

- **Toolbar:** Keep **Heading**, **Bold**, **Italic**, **Underline**, **Alignment** (left, center, right, justify), **Numbered list**, **Bulleted list**, **Insert table**, **Horizontal line**, and **Source editing** (so power users can tweak HTML).
- **Explicit numbering:** Rely on the user typing “1.” “2.” “3.” in the text; avoid converting those to a single “list” block that might change numbering style in DOCX.
- **Two-column:** Prefer **table** (2 columns) for the claimant/attorney block so export is consistent.

---

## 4. Optional Backend Enhancements

- **Indent from HTML:** In `html_to_docx`, detect `style="margin-left: ..."` or a class (e.g. `indent-caption`) and set `paragraph_format.left_indent` so the “In the Matter of…” block can be indented without using a table.
- **Tab characters:** If you want to support tabs in CKEditor (e.g. one line with “Firm name\tClaimant name”), extend the HTML parser to preserve `\t` and add default tab stops (e.g. center at 3.25", right at 6.5") for that paragraph so the DOCX aligns like the template pipeline.
- **More legal titles:** Add any other standard titles (e.g. “NOTICE OF CLAIM”) to `_legal_paragraph_format` in `formatting/utils/html_to_docx.py` so they automatically get center + bold when the paragraph text matches.

---

## 5. Summary

- Use **CKEditor 5** for WYSIWYG: alignment, bold, explicit “1.” “2.” “3.”, and a **2-column table** for the claimant/attorney block.
- Use **Download as DOCX** (`/ckeditor/api/export-docx`); the existing `html_to_docx` pipeline already handles alignment, bold, numbering indent, tables, and NOTICE OF CLAIM–style title formatting.
- For template-faithful layout (exact margins, tab stops, styles from a DOCX template), continue using the **Streamlit “Format with LLM”** flow with a NOTICE OF CLAIM template; then optionally paste the result into CKEditor for small edits and re-export.
