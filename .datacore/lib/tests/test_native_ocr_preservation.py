"""Native OCR preserves text pages and scanned pages in the same document.

Requires the OCR Python dependencies, Tesseract and Poppler. CI installs them
explicitly; absence is a failed verification, not a skipped preservation test.
"""
import importlib.util
from pathlib import Path


def test_native_image_and_mixed_pdf_preserve_both_sources(tmp_path):
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader
    script=Path(__file__).resolve().parents[1]/'ocr-server/server.py'
    spec=importlib.util.spec_from_file_location('audit_native_ocr',script)
    ocr=importlib.util.module_from_spec(spec);spec.loader.exec_module(ocr)
    image=Image.new('RGB',(1400,300),'white')
    font=ImageFont.load_default(size=72)
    ImageDraw.Draw(image).text((30,70),'SAFE SCANNED PAGE',fill='black',font=font)
    image_path=tmp_path/'sample.png';image.save(image_path)
    assert 'SAFE SCANNED PAGE' in ocr.extract_text_from_image(str(image_path))
    pdf_path=tmp_path/'mixed.pdf';pdf=canvas.Canvas(str(pdf_path))
    for line in range(10):
        pdf.drawString(40,750-line*20,'native preservation alpha beta gamma delta epsilon zeta')
    pdf.showPage();pdf.drawImage(ImageReader(image),40,500,width=500,height=107);pdf.save()
    text=ocr.extract_text_from_pdf(str(pdf_path),dpi=144,max_pages=2)
    assert 'native preservation' in text and 'SAFE SCANNED PAGE' in text
