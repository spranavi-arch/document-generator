# Pleading Template Guide

Pixel-perfect output comes from **cloning Word styles and injecting content into those styles**—not from recreating formatting in code.

---

## Step 1 — Create a master DOCX template

In Word, create **pleading_template.docx** and define:

### Styles (use these exact names for pixel-perfect matching)

| Style name | Use for |
|------------|--------|
| **CaptionStyle** | Court name, county, parties, index no., document title; also separator line (CaptionLine) |
| **HeadingStyle** | Section headings (e.g. VERIFIED COMPLAINT, FIRST CAUSE OF ACTION) |
| **AllegationStyle** | Numbered allegation/body paragraphs (Word handles numbering and indentation) |
| **SignatureStyle** | Signature block (attorney name, firm, address) |

The app prefers these names but will also use: Caption, Heading 1, List Number, Signature, etc., if present.

### In Word

1. **Define the styles** (Home → Styles → Create New Style / Modify):
   - **CaptionStyle** — font, alignment, spacing for caption and separator line.
   - **HeadingStyle** — bold, capitalization, spacing for section titles.
   - **AllegationStyle** — set **numbering** and **indentation** here (hanging indent, list style). Word handles numbering and indentation; the app never simulates them.
   - **SignatureStyle** — font and spacing for the signature block.

2. **Set margins** (Layout → Margins) for the whole document.

3. **Set tab stops** in the styles that need them (e.g. Caption for right-aligned index/date).

4. **Numbered list style** — attach a list definition to **Allegation** (or your “numbered” style) so that:
   - Indentation is controlled by the style
   - Numbering is automatic (1., 2., 3.)

5. **Separators** — add a paragraph with the separator text (e.g. dashes ending in X) and apply **CaptionStyle** (or a caption/line style). The app injects into that style only; it never hardcodes separator text.

Save as **pleading_template.docx**.

---

## Step 2 — Inject text programmatically

Using **python-docx**, you **do not** format text manually. You **apply styles**. Word handles indentation, numbering, and spacing from the style definitions.

### Example — numbered allegation

```python
from docx import Document

doc = Document("pleading_template.docx")

p = doc.add_paragraph(
    "That on August 11th, 2022...",
    style="AllegationStyle"
)

doc.save("output.docx")
```

Word handles:

- ✔ Indentation  
- ✔ Numbering  
- ✔ Spacing  

### Example — caption separator

Define the separator in the template (style + sample text). Then inject with that style only; never hardcode the line in code:

```python
# Template defines CaptionStyle and a sample separator paragraph; app uses that style
doc.add_paragraph(sep_text_from_template_or_empty, style="CaptionStyle")
```

### Example — signature block

```python
doc.add_paragraph("MICHAEL COHAN, ESQ.", style="SignatureStyle")
```

---

## How this app uses your template

1. **Upload** your **pleading_template.docx** in the app.
2. The app **extracts** style names and template structure (including which paragraphs use Caption, CaptionLine, Allegation, Heading, Signature).
3. You paste **raw text** and click **Format with LLM**.
4. The app **injects** content by applying **your template’s styles only**:
   - `doc.add_paragraph(text, style="Allegation")` for allegations
   - `doc.add_paragraph(sep, style="CaptionLine")` for separators
   - etc.
5. **No manual formatting** is applied in code—indentation, numbering, tab stops, and spacing come from the **style definitions** in your DOCX.

**Rules enforced by the app:**

- **Do not format text manually.** Insert all content into predefined Word styles (CaptionStyle, AllegationStyle, HeadingStyle, SignatureStyle).
- **Never simulate indentation** with spaces or manual paragraph_format when using template styles.
- **Never simulate numbering.** Word handles it via the style (e.g. AllegationStyle with list definition).
- **Never hardcode separators.** Use the template’s style and sample text only.
- **All formatting must come from template styles.**

---

## Summons / court document formatting (caption, double lines, signature, checkboxes)

To get output that looks like a formal summons (court heading, caption with dashed line, double-line separators, law firm block, signature line, checkboxes, notices):

### 1. Build the template in Word

Create a DOCX that already looks like your target. Use **one named style per kind of block**:

| What you see | In Word | Style name (example) | App behavior |
|--------------|---------|----------------------|--------------|
| Court + county (e.g. SUPREME COURT… COUNTY OF KINGS) | Left-aligned, uppercase | Caption, CaptionStyle, or similar | Extracted; LLM assigns caption/section_header |
| Dashed line with -X | Paragraph with dashes + “-X” | Same caption/line style | Use **line** or **signature_line** block_type; template text is kept or sampled |
| Index No. / document title (right side) | Right-aligned, tab stops if needed | Caption or dedicated style | Caption; alignment from template |
| “-against-” / party names | Centered or indented | Caption / body | Section header or paragraph |
| **SUMMONS AND VERIFIED COMPLAINT** (main title) | Bold, centered | Heading, Title, Heading 1 | Heading → centered |
| Double horizontal lines | Paragraph with bottom (and maybe top) border, or a “line” paragraph | Line style or section_underline | **line** or **section_underline**; we can add thin border for section_underline |
| Law firm name + “Attorneys for Plaintiff” (italic) | Centered, bold + italic | SignatureStyle, Caption, or custom | Signature/address → left; style keeps center/italic from template |
| Certification text | Left-aligned | Normal / body | Paragraph → justify (or keep left from template) |
| Signature line (_______) | Underline or literal underscores | Signature line style | **signature_line** block_type; uses template line sample |
| □ NOTICE OF ENTRY / NOTICE OF SETTLEMENT | Checkbox + bold label + indented text | Body / list style | Use **[ ]** and **[x]** in your raw text; app renders ☐ / ☑ |
| Page number at bottom | Centered | Footer in Word | Set in Word template; footer is preserved |

### 2. Lines and separators

- **Dashed line with -X:** In the template, add a paragraph that contains the exact line (e.g. `_________________________ -X` or dashes). Give it a style (e.g. CaptionLine, or the same as caption). The app extracts that as a **line** sample and will reuse it (or the text you provide) for **line** / **signature_line** blocks.
- **Double rules:** Use a paragraph with **bottom border** (and optionally top) and no text, or a short line of underscores. Map it to **section_underline** or a line style so the LLM can emit that block_type.

### 3. Checkboxes

In your **raw text** (the text you paste before “Format with LLM”), write:

- `[ ]` for an empty checkbox (renders as ☐)
- `[x]` or `[X]` for a checked checkbox (renders as ☑)

The app replaces these before writing to the DOCX. No need to put real checkbox content controls in the template unless you want to edit them in Word later.

### 4. Alignment

The app applies alignment by block type after applying your style:

- **Caption / section headers:** center (when block_type is heading or section_header).
- **Body / allegations:** justify.
- **Signature, address, “TO THE…”:** left.
- **Line / signature_line:** left.

Your template’s style definitions (bold, italic, font, spacing, borders) are preserved; alignment may be overridden so captions and signatures look correct.

### 5. Upload and run

1. Save the DOCX as your **template** (single-column layout is best; the app will convert it to single-column for page images if needed).
2. **Upload** that template in the app.
3. Paste your **raw text** (with [ ] / [x] for checkboxes where needed).
4. Click **Format with LLM**. The model uses the template **page images** and style list to assign block_types; the formatter injects text into your styles and applies line/signature_line/checkboxes as above.
5. Download the result and tweak the template (styles, borders, line samples) until the layout matches your target.

---

## Why the output can look different from what you pasted

- **You pasted multiple versions or copies.** If your "generated text" is several pleadings or drafts pasted one after another (e.g. summons + complaint + another caption + summons again + allegations + …), the formatter will segment and render all of it. The app does not merge them into one short document. **Paste only the single document you want** (one caption, one body, one signature block) so the output matches that.
- **Long duplicate paragraphs are now collapsed.** If the same long paragraph (e.g. the same summons or caption text) appears more than once, the formatter skips repeats after the first so you don’t get the same block many times. Short lines (e.g. "Plaintiff," or "Dated: [date]") can still repeat.
- **Template and LLM control structure.** The template’s styles and the LLM’s segmentation (with template page images) decide where page breaks and headings go. Use a template that matches the single-document layout you want.

---

## Optional: disable style-only injection

By default the app uses **style-only** injection when a template structure is present (recommended for pixel-perfect matching). To make the app re-apply extracted paragraph/run format from the template instead, set:

```bash
INJECT_STYLE_ONLY=0
```

Then the app will copy paragraph format and run format from each template paragraph onto the injected content.
