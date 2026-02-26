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


def replace_marker_across_runs(paragraph, marker: str, value: str) -> bool:
    """
    Replace a placeholder marker that may be split across runs (e.g. Run1 '{{LIC' Run2 'ENSE_PLATE}}').
    Concatenates run texts, finds marker, then rewrites the first involved run with the
    replaced text and clears the others. Returns True if replacement was made.
    """
    full = "".join(r.text or "" for r in paragraph.runs)
    idx = full.find(marker)
    if idx < 0:
        return False
    end = idx + len(marker)

    pos = 0
    run_spans = []
    for ri, r in enumerate(paragraph.runs):
        run_len = len(r.text or "")
        run_spans.append((ri, pos, pos + run_len))
        pos += run_len

    involved = [s for s in run_spans if not (s[2] <= idx or s[1] >= end)]
    if not involved:
        return False

    first_i = involved[0][0]
    last_i = involved[-1][0]

    new_full = full[:idx] + value + full[end:]

    paragraph.runs[first_i].text = new_full
    for ri in range(first_i + 1, len(paragraph.runs)):
        if ri <= last_i:
            paragraph.runs[ri].text = ""
    return True


def _replace_placeholder_in_paragraph(p, replacements: dict[str, str]) -> None:
    """Replace placeholders in one paragraph. Run-safe: rebuild paragraph so placeholders
    that span multiple runs (e.g. Run1 '{{PLA' Run2 'INTIFF' Run3 '}}') are fully replaced.
    Prevents style leakage by adding a single clean run (no bold/italic/underline).
    Only replaces placeholder markers (e.g. {{PLAINTIFF_NAME}}), never raw document text.
    Uses replace_marker_across_runs for any marker not found in paragraph text (split across runs)."""
    full_text = p.text
    # First pass: run-based replace for markers that may be split across runs
    for key, value in replacements.items():
        if key not in full_text:
            replace_marker_across_runs(p, key, value)
    full_text = p.text
    replaced = False
    for key, value in replacements.items():
        if key in full_text:
            full_text = full_text.replace(key, value)
            replaced = True
    if not replaced:
        return
    for r in p.runs[::-1]:
        r._element.getparent().remove(r._element)
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
