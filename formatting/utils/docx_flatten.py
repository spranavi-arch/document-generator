"""Flatten a Word document by removing content controls (SDT) and optional form-field interactivity.

Flattening replaces interactive elements with their current content so the document
is static (no fill-in fields, dropdowns, or content-control wrappers). Text boxes
and shapes are not fully supported by python-docx and are left as-is.

Example:
    from docx import Document
    from utils.docx_flatten import flatten_document

    doc = Document("input.docx")
    flatten_document(doc)
    doc.save("flattened_output.docx")
"""

from docx import Document
from docx.oxml.ns import qn


def _unwrap_sdt(sdt_el):
    """Replace one w:sdt element with its w:sdtContent children. Returns True if unwrapped."""
    parent = sdt_el.getparent()
    if parent is None:
        return False
    sdt_content = sdt_el.find(qn("w:sdtContent"))
    if sdt_content is None:
        parent.remove(sdt_el)
        return True
    idx = list(parent).index(sdt_el)
    children = list(sdt_content)
    parent.remove(sdt_el)
    for i, child in enumerate(children):
        parent.insert(idx + i, child)
    return True


def _flatten_element(element):
    """Unwrap all w:sdt descendants under element (in-place). Repeats until no SDT left (handles nesting)."""
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    while True:
        sdts = element.xpath(".//w:sdt", namespaces=ns)
        if not sdts:
            break
        _unwrap_sdt(sdts[0])


def flatten_document(doc: Document) -> None:
    """
    Flatten the document in-place: remove content controls (SDT) by replacing each
    with its inner content so the text and layout remain but interactivity is removed.

    - Content controls (w:sdt): unwrapped so only w:sdtContent content remains.
    - Form fields: in Word OOXML these are often implemented as content controls;
      unwrapping SDTs removes that layer. Legacy form fields (w:fldChar, etc.) may
      require additional handling; this function focuses on SDT.
    - Text boxes / shapes: python-docx does not expose a simple API for these; they
      are left unchanged. To strip text from shapes you would need lower-level XML
      or a different library.

    Does not save the document; call doc.save(output_path) after.
    """
    body = doc.element.body
    _flatten_element(body)
    for section in doc.sections:
        try:
            header = section.header
            if header and header._element is not None:
                _flatten_element(header._element)
        except Exception:
            pass
        try:
            footer = section.footer
            if footer and footer._element is not None:
                _flatten_element(footer._element)
        except Exception:
            pass


def flatten_word_doc(input_path: str, output_path: str) -> None:
    """
    Load a DOCX, flatten it (remove content controls / SDT), and save to a new file.
    Convenience wrapper around flatten_document().

    Example:
        flatten_word_doc("input.docx", "flattened_output.docx")
    """
    doc = Document(input_path)
    flatten_document(doc)
    doc.save(output_path)
