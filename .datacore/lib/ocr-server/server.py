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
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from public_download import download

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_PIXELS = 25_000_000
MAX_PAGES = 20

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


def _ocr_pil_image(img, language="eng") -> str:
    """Use a private canonical image path at the native-parser boundary."""
    import pytesseract
    if img.width * img.height > MAX_PIXELS:
        raise ValueError("image exceeds pixel limit")
    # pytesseract's PIL-object path creates an input using an unresolved /tmp
    # alias. Leptonica can fail to open that path on macOS, then tries to read
    # PNG bytes as a list of filenames. An explicit resolved input also keeps
    # native parsing on the exact image whose dimensions were checked.
    with tempfile.TemporaryDirectory(prefix="datacore-ocr-") as directory:
        source = Path(directory).resolve() / "input.png"
        img.save(source, format="PNG")
        return pytesseract.image_to_string(str(source), lang=language, timeout=30)


def _image_text(source, language):
    from PIL import Image
    with Image.open(source) as img:
        return _ocr_pil_image(img, language)


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
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("image exceeds size limit")
        text = _image_text(path, language)
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
        data = download(url, max_bytes=MAX_INPUT_BYTES)
    except Exception:
        return "Download failed: URL unavailable, unsafe, or exceeds limits"

    try:
        text = _image_text(io.BytesIO(data), language)
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
        max_pages: Limit pages processed (1–20; None uses 20).

    Returns:
        Extracted text with page separators, or an error message.
    """
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not 72 <= dpi <= 300:
        return "Error: dpi must be an integer between 72 and 300"
    if max_pages is None:
        max_pages = MAX_PAGES
    if isinstance(max_pages, bool) or not isinstance(max_pages, int) or not 1 <= max_pages <= MAX_PAGES:
        return "Error: max_pages must be an integer between 1 and 20"
    path = Path(pdf_path).resolve()
    if not path.exists():
        return f"Error: File not found: {pdf_path}"
    if not path.is_file():
        return f"Error: Not a file: {pdf_path}"
    if path.stat().st_size > MAX_INPUT_BYTES:
        return "Error: PDF exceeds size limit"

    # --- Step 1: Try native text extraction via pdftotext ---
    native_text = _try_pdftotext(str(path), max_pages)

    native_pages = native_text.split("\f") if native_text else []
    if native_pages and not native_pages[-1].strip():
        native_pages.pop()  # pdftotext terminates the final page with form feed.
    if native_pages and all(_word_count(page) >= 50 for page in native_pages):
        return native_text.strip() + f"\n\n[Extraction limited to the first {max_pages} pages.]"

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

    return _ocr_pdf_pages(str(path), language=language, dpi=dpi, max_pages=max_pages, native_pages=native_pages)


def _bounded_output(command, *, timeout=60, limit=2 * 1024 * 1024):
    """Bound both pipe memory and runtime, including malformed-document output."""
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        output = bytearray()
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("text extraction deadline exceeded")
                readable, _, _ = select.select([process.stdout], [], [], remaining)
                if not readable:
                    raise TimeoutError("text extraction deadline exceeded")
                chunk = os.read(process.stdout.fileno(), min(65536, limit + 1 - len(output)))
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > limit:
                    raise ValueError("extracted text exceeds size limit")
            code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            return code, output.decode("utf-8")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _try_pdftotext(pdf_path: str, max_pages: Optional[int]) -> Optional[str]:
    """Use pdftotext (poppler) for native text extraction."""
    try:
        cmd = ["pdftotext", "-layout"]
        if max_pages is not None:
            cmd += ["-l", str(max_pages)]
        cmd += [pdf_path, "-"]
        code, output = _bounded_output(cmd)
        if code == 0:
            return output
    except (FileNotFoundError, subprocess.TimeoutExpired, TimeoutError):
        pass
    return None


def _word_count(text: str) -> int:
    return len(text.split())


def _ocr_pdf_pages(
    pdf_path: str,
    language: str = "eng",
    dpi: int = 300,
    max_pages: Optional[int] = None,
    native_pages: Optional[list[str]] = None,
) -> str:
    """
    Render PDF pages to images with pdf2image and OCR each page.
    This is the upstream fix: scanned PDFs now yield text instead of silence.
    """
    from pdf2image import convert_from_path, pdfinfo_from_path
    import pytesseract

    try:
        count = int(pdfinfo_from_path(pdf_path, timeout=10)["Pages"])
        limit = min(count, max_pages or MAX_PAGES)
    except Exception as e:
        return f"PDF metadata failed: {e}"
    page_texts = []
    for number in range(1, limit + 1):
        native = (native_pages or [])[number - 1] if number <= len(native_pages or []) else ""
        if _word_count(native) >= 50:
            page_texts.append(f"--- Page {number} ---\n{native.strip()}")
            continue
        try:
            # Render only one page at a time: a small compressed PDF must not
            # allocate all expanded page images simultaneously.
            images = convert_from_path(pdf_path, dpi=dpi, fmt="PNG", timeout=60,
                                       size=5000, thread_count=1,
                                       first_page=number, last_page=number)
        except Exception as e:
            return f"PDF rendering failed at page {number}: {e}"
        for img in images:
            try:
                if img.width * img.height > MAX_PIXELS:
                    raise ValueError("page exceeds pixel limit")
                text = _ocr_pil_image(img, language)
                page_texts.append(f"--- Page {number} ---\n{text.strip()}")
            except Exception as e:
                page_texts.append(f"--- Page {number} ---\n[OCR failed: {e}]")
            finally:
                img.close()
    if count > limit:
        page_texts.append(f"[Limited to first {limit} of {count} pages; split the PDF to process the remainder.]")
    return "\n\n".join(page_texts) or "[No pages rendered from PDF]"


if __name__ == "__main__":
    mcp.run()
