"""
Datacore OCR MCP Server
Forked from Liquid4All/cookbook LocalCowork OCR server.

Provides OCR tools via Tesseract (primary) with graceful degradation
when system deps are missing. Fixes upstream scanned-PDF gap with
pdf2image-based page rendering.

Upstream gap fixed here: extract_text_from_pdf now handles image-only
PDFs via pdf2image → pytesseract, contributing back to LocalCowork.
"""

from __future__ import annotations

import base64
import io
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ocr")


def _check_tesseract() -> bool:
    try:
        result = subprocess.run(
            ["tesseract", "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_pdf2image() -> bool:
    try:
        import pdf2image  # noqa: F401
        return True
    except ImportError:
        return False


def _check_pytesseract() -> bool:
    try:
        import pytesseract  # noqa: F401
        return True
    except ImportError:
        return False


def _ocr_pil_image(img) -> str:
    """Run pytesseract on a PIL image, return extracted text."""
    import pytesseract
    return pytesseract.image_to_string(img)


def _availability_error(what: str) -> str:
    return (
        f"OCR unavailable: {what} not installed.\n"
        "Install with:\n"
        "  sudo apt install tesseract-ocr poppler-utils\n"
        "  pip install pytesseract pdf2image\n"
        "Then restart the MCP server."
    )


@mcp.tool()
def check_ocr_availability() -> dict:
    """
    Check which OCR components are installed and available.

    Returns a status dict with keys: tesseract_binary, pytesseract,
    pdf2image, and a ready flag indicating full OCR capability.
    """
    t_bin = _check_tesseract()
    t_pkg = _check_pytesseract()
    pdf2 = _check_pdf2image()
    return {
        "tesseract_binary": t_bin,
        "pytesseract": t_pkg,
        "pdf2image": pdf2,
        "ready": t_bin and t_pkg and pdf2,
        "image_ocr_ready": t_bin and t_pkg,
        "pdf_ocr_ready": t_bin and t_pkg and pdf2,
        "install_hint": (
            None
            if (t_bin and t_pkg and pdf2)
            else "sudo apt install tesseract-ocr poppler-utils && pip install pytesseract pdf2image"
        ),
    }


@mcp.tool()
def extract_text_from_image(image_path: str, language: str = "eng") -> str:
    """
    Extract text from a local image file using Tesseract OCR.

    Args:
        image_path: Absolute path to the image file (.png, .jpg, .jpeg, .tiff, .bmp, .gif, .webp)
        language: Tesseract language code, e.g. 'eng', 'deu', 'fra'. Defaults to 'eng'.

    Returns:
        Extracted text as a string, or an error message if OCR is unavailable.
    """
    if not _check_pytesseract():
        return _availability_error("pytesseract")
    if not _check_tesseract():
        return _availability_error("tesseract binary")

    path = Path(image_path)
    if not path.exists():
        return f"Error: File not found: {image_path}"
    if not path.is_file():
        return f"Error: Not a file: {image_path}"

    try:
        from PIL import Image
        img = Image.open(path)
        import pytesseract
        text = pytesseract.image_to_string(img, lang=language)
        return text.strip() if text.strip() else "[No text detected in image]"
    except Exception as e:
        return f"OCR failed: {e}"


@mcp.tool()
def extract_text_from_image_url(url: str, language: str = "eng") -> str:
    """
    Download an image from a URL and extract its text using Tesseract OCR.

    Args:
        url: HTTP/HTTPS URL pointing to an image file
        language: Tesseract language code. Defaults to 'eng'.

    Returns:
        Extracted text as a string, or an error message.
    """
    if not _check_pytesseract():
        return _availability_error("pytesseract")
    if not _check_tesseract():
        return _availability_error("tesseract binary")

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Datacore-OCR/1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = resp.read()
    except Exception as e:
        return f"Download failed: {e}"

    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        import pytesseract
        text = pytesseract.image_to_string(img, lang=language)
        return text.strip() if text.strip() else "[No text detected in image]"
    except Exception as e:
        return f"OCR failed: {e}"


@mcp.tool()
def extract_text_from_pdf(
    pdf_path: str,
    language: str = "eng",
    dpi: int = 300,
    max_pages: Optional[int] = None,
) -> str:
    """
    Extract text from a PDF, including scanned (image-only) PDFs.

    Strategy:
    1. Try pdftotext (poppler) for native-text PDFs — fast and accurate.
    2. If text is sparse (<50 words), fall back to pdf2image + Tesseract OCR.
       This is the fix for scanned PDFs that upstream LocalCowork was missing.

    Args:
        pdf_path: Absolute path to the PDF file.
        language: Tesseract language code for OCR fallback. Defaults to 'eng'.
        dpi: Resolution for PDF→image rendering. Higher = better quality but slower.
             Defaults to 300 (good balance for A4 documents).
        max_pages: Limit pages processed (None = all pages).

    Returns:
        Extracted text with page separators, or an error message.
    """
    path = Path(pdf_path)
    if not path.exists():
        return f"Error: File not found: {pdf_path}"
    if not path.is_file():
        return f"Error: Not a file: {pdf_path}"

    # --- Step 1: Try native text extraction via pdftotext ---
    native_text = _try_pdftotext(str(path), max_pages)

    if native_text and _word_count(native_text) >= 50:
        return native_text.strip()

    # --- Step 2: Scanned PDF fallback — pdf2image + Tesseract ---
    if not _check_pdf2image():
        if native_text:
            return (
                native_text.strip()
                + "\n\n[Note: pdf2image not installed — scanned pages skipped. "
                "Install with: pip install pdf2image && sudo apt install poppler-utils]"
            )
        return _availability_error("pdf2image (required for scanned PDFs)")

    if not _check_pytesseract():
        return _availability_error("pytesseract")
    if not _check_tesseract():
        return _availability_error("tesseract binary")

    return _ocr_pdf_pages(str(path), language=language, dpi=dpi, max_pages=max_pages)


def _try_pdftotext(pdf_path: str, max_pages: Optional[int]) -> Optional[str]:
    """Use pdftotext (poppler) for native text extraction."""
    try:
        cmd = ["pdftotext", "-layout"]
        if max_pages is not None:
            cmd += ["-l", str(max_pages)]
        cmd += [pdf_path, "-"]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            return result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _word_count(text: str) -> int:
    return len(text.split())


def _ocr_pdf_pages(
    pdf_path: str,
    language: str = "eng",
    dpi: int = 300,
    max_pages: Optional[int] = None,
) -> str:
    """
    Render PDF pages to images with pdf2image and OCR each page.
    This is the upstream fix: scanned PDFs now yield text instead of silence.
    """
    from pdf2image import convert_from_path
    import pytesseract

    try:
        kwargs: dict = {"dpi": dpi, "fmt": "PNG"}
        if max_pages is not None:
            kwargs["last_page"] = max_pages

        images = convert_from_path(pdf_path, **kwargs)
    except Exception as e:
        return f"PDF rendering failed: {e}"

    page_texts = []
    for i, img in enumerate(images, start=1):
        try:
            text = pytesseract.image_to_string(img, lang=language)
            page_texts.append(f"--- Page {i} ---\n{text.strip()}")
        except Exception as e:
            page_texts.append(f"--- Page {i} ---\n[OCR failed: {e}]")

    if not page_texts:
        return "[No pages rendered from PDF]"

    return "\n\n".join(page_texts)


if __name__ == "__main__":
    mcp.run()
