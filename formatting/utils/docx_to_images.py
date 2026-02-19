"""Convert a DOCX to one image per page for LLM vision input.

Flow (as used by the formatter):
  1. Convert document to images: each page rendered as an image (DOCX → PDF → PNG).
  2. Send images to the LLM: pages are passed to the vision API as the primary formatting reference.
  3. Use model for formatting reference: the LLM analyzes layout (headers, margins, spacing, structure)
     and segments/format the raw text to match the template.

Conversion: LibreOffice headless (DOCX→PDF), then either PyMuPDF or pdf2image+Pillow (PDF→PNG).
Optional: Tesseract OCR can be run on each page image to extract text for image-heavy or scanned docs.
"""

import base64
import io
import os
import shutil
import subprocess
import tempfile


def _find_libreoffice() -> str | None:
    """Return path to LibreOffice executable (soffice or libreoffice), or None."""
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    if os.name == "nt":
        for base in (
            os.path.expandvars(r"%ProgramFiles%\LibreOffice\program"),
            os.path.expandvars(r"%ProgramFiles(x86)%\LibreOffice\program"),
        ):
            exe = os.path.join(base, "soffice.exe")
            if os.path.isfile(exe):
                return exe
    return None


def _docx_to_pdf(docx_path: str) -> str | None:
    """Convert DOCX to PDF using LibreOffice. Returns path to PDF or None."""
    lo = _find_libreoffice()
    if not lo:
        return None
    out_dir = tempfile.mkdtemp()
    try:
        subprocess.run(
            [
                lo,
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                out_dir,
                os.path.abspath(docx_path),
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        base = os.path.splitext(os.path.basename(docx_path))[0]
        pdf_path = os.path.join(out_dir, base + ".pdf")
        if os.path.isfile(pdf_path):
            return pdf_path
        return None
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        try:
            for f in os.listdir(out_dir):
                os.unlink(os.path.join(out_dir, f))
            os.rmdir(out_dir)
        except OSError:
            pass


def _pdf_to_page_images_fitz(pdf_path: str, dpi: int, max_pages: int) -> list[bytes]:
    """Render PDF to PNG bytes using PyMuPDF (fitz). Returns list of PNG bytes."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []
    out = []
    try:
        doc = fitz.open(pdf_path)
        n = min(len(doc), max_pages)
        for i in range(n):
            page = doc[i]
            mat = fitz.Matrix(dpi / 72, dpi / 72)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            out.append(pix.tobytes("png"))
        doc.close()
    except Exception:
        pass
    return out


def _pdf_to_page_images_pdf2image(pdf_path: str, dpi: int, max_pages: int) -> list[bytes]:
    """Render PDF to PNG bytes using pdf2image (Pillow + poppler). Returns list of PNG bytes."""
    try:
        from pdf2image import convert_from_path
    except ImportError:
        return []
    out = []
    try:
        pil_images = convert_from_path(pdf_path, dpi=dpi)
        for i, img in enumerate(pil_images):
            if i >= max_pages:
                break
            bio = io.BytesIO()
            img.save(bio, "PNG")
            out.append(bio.getvalue())
    except Exception:
        pass
    return out


def docx_to_page_images(docx_path: str, dpi: int = 150, max_pages: int = 15) -> list[bytes]:
    """
    Convert a DOCX file to one PNG image per page.
    Uses LibreOffice for DOCX→PDF, then PyMuPDF (preferred) or pdf2image+Pillow for PDF→PNG.
    Returns list of PNG bytes; empty list if conversion fails (e.g. LibreOffice not installed).
    """
    pdf_path = _docx_to_pdf(docx_path)
    if not pdf_path:
        return []
    out_images = _pdf_to_page_images_fitz(pdf_path, dpi, max_pages)
    if not out_images:
        out_images = _pdf_to_page_images_pdf2image(pdf_path, dpi, max_pages)
    pdf_dir = os.path.dirname(pdf_path)
    docx_dir = os.path.dirname(os.path.abspath(docx_path))
    if pdf_dir != docx_dir:
        try:
            os.unlink(pdf_path)
            try:
                os.rmdir(pdf_dir)
            except OSError:
                pass
        except OSError:
            pass
    return out_images


def docx_to_page_images_base64(docx_path: str, dpi: int = 150, max_pages: int = 15) -> list[str]:
    """Same as docx_to_page_images but returns base64-encoded strings for use in image_url."""
    raw = docx_to_page_images(docx_path, dpi=dpi, max_pages=max_pages)
    return [base64.b64encode(b).decode("ascii") for b in raw]


def ocr_page_images(page_images: list[bytes]) -> list[str]:
    """
    Run Tesseract OCR on each page image to extract text.
    Useful for scanned documents or image-heavy templates.
    Returns list of text strings (one per page); empty list if pytesseract or Tesseract is not available.
    """
    try:
        import pytesseract
    except ImportError:
        return []
    from PIL import Image
    out = []
    for i, png_bytes in enumerate(page_images):
        try:
            img = Image.open(io.BytesIO(png_bytes))
            text = pytesseract.image_to_string(img)
            out.append((text or "").strip())
        except Exception:
            out.append("")
    return out
