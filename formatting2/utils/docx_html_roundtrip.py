"""DOCX → HTML → (optional modify) → DOCX round-trip pipeline.

Use this when you want to:
- Render a DOCX as HTML for web display or editing
- Change formatting/structure in HTML (e.g. with BeautifulSoup)
- Export the result back to DOCX for download

Example:
    from utils.docx_to_html import docx_to_html
    from utils.html_to_docx import html_to_docx_bytes
    from utils.docx_html_roundtrip import process_docx_roundtrip, modify_html_with_soup

    # Full pipeline with optional HTML modification
    process_docx_roundtrip("input.docx", "output.docx", modify_fn=lambda html: modify_html_with_soup(html, add_para_class="legal"))

    # Or step by step
    html = docx_to_html("input.docx")
    html = modify_html_with_soup(html, add_para_class="modified-text")
    docx_bytes = html_to_docx_bytes(html)
    with open("output.docx", "wb") as f:
        f.write(docx_bytes)
"""

from __future__ import annotations

from typing import Callable


def modify_html_with_soup(
    html: str,
    *,
    add_para_class: str | None = None,
    add_wrapper_class: str | None = None,
) -> str:
    """
    Modify HTML using BeautifulSoup. Optional: add a class to all <p> tags or wrap body in a div.

    Args:
        html: HTML string.
        add_para_class: If set, add this class to every <p> element.
        add_wrapper_class: If set, wrap the contents in a div with this class (if parsing yields a body, wrap its children).

    Returns:
        Modified HTML string. Returns original if BeautifulSoup is not installed.
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return html

    soup = BeautifulSoup(html, "html.parser")
    if add_para_class:
        for p in soup.find_all("p"):
            p["class"] = p.get("class", []) or []
            if isinstance(p["class"], str):
                p["class"] = [p["class"]]
            if add_para_class not in p["class"]:
                p["class"].append(add_para_class)
    if add_wrapper_class:
        body = soup.find("body")
        container = body if body else soup
        wrapper = soup.new_tag("div", **{"class": add_wrapper_class})
        for child in list(container.children):
            if hasattr(child, "extract"):
                wrapper.append(child.extract())
            else:
                wrapper.append(child)
        container.append(wrapper)
    return str(soup)


def process_docx_roundtrip(
    input_docx: str,
    output_docx: str,
    *,
    modify_fn: Callable[[str], str] | None = None,
) -> None:
    """
    Convert DOCX to HTML, optionally modify the HTML, then convert back to DOCX and save.

    Args:
        input_docx: Path to the source .docx file.
        output_docx: Path to write the resulting .docx file.
        modify_fn: Optional function that takes the HTML string and returns modified HTML.
                   Example: lambda html: modify_html_with_soup(html, add_para_class="legal")
    """
    from utils.docx_to_html import docx_to_html
    from utils.html_to_docx import html_to_docx_bytes

    html = docx_to_html(input_docx)
    if modify_fn:
        html = modify_fn(html)
    docx_bytes = html_to_docx_bytes(html)
    with open(output_docx, "wb") as f:
        f.write(docx_bytes)


def process_docx_roundtrip_to_bytes(
    input_docx: str | bytes,
    *,
    modify_fn: Callable[[str], str] | None = None,
) -> bytes:
    """
    Same as process_docx_roundtrip but returns DOCX bytes instead of writing to a file.
    input_docx can be a file path or DOCX bytes.
    """
    from io import BytesIO

    from utils.docx_to_html import docx_to_html
    from utils.html_to_docx import html_to_docx_bytes

    html = docx_to_html(input_docx)
    if modify_fn:
        html = modify_fn(html)
    return html_to_docx_bytes(html)
