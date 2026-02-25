"""Fixed-template document generation: open template, replace placeholders, save.

Use this when each document type has:
- One fixed layout
- One fixed caption geometry
- One fixed divider structure
- One fixed signature block

No geometry cloning, style extraction, or paragraph rebuilding. Only swap text.
Run-safe: placeholders that span multiple runs are replaced by rebuilding the paragraph.
"""

from pathlib import Path

from docx import Document


def _replace_placeholder_in_paragraph(p, replacements: dict[str, str]) -> None:
    """Replace placeholders in one paragraph. Run-safe: rebuild paragraph so placeholders
    that span multiple runs (e.g. Run1 '{{PLA' Run2 'INTIFF' Run3 '}}') are fully replaced.
    Prevents style leakage by adding a single clean run (no bold/italic/underline)."""
    full_text = p.text
    replaced = False
    for key, value in replacements.items():
        if key in full_text:
            full_text = full_text.replace(key, value)
            replaced = True
    if not replaced:
        return
    # Remove all runs (reverse order so indices stay valid)
    for r in p.runs[::-1]:
        r._element.getparent().remove(r._element)
    # Add single clean run so template italic/alignment don't leak
    new_run = p.add_run(full_text)
    new_run.bold = False
    new_run.italic = False
    new_run.underline = False


def replace_placeholders(doc: Document, replacements: dict[str, str]) -> None:
    """Replace {{KEY}} placeholders in all paragraphs and table cells. Run-safe:
    when a placeholder is found in paragraph text, the paragraph is rebuilt with
    one clean run to avoid partial replacement and style leakage."""
    for p in doc.paragraphs:
        _replace_placeholder_in_paragraph(p, replacements)
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    _replace_placeholder_in_paragraph(p, replacements)


def generate_from_template(
    template_path: str | Path,
    replacements: dict[str, str],
    output_path: str | Path | None = None,
) -> Document:
    """
    Load a DOCX template, replace all placeholders, optionally save to file.
    Returns the modified Document so you can add dynamic sections or save elsewhere.
    """
    doc = Document(template_path)
    replace_placeholders(doc, replacements)
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        doc.save(output_path)
    return doc
