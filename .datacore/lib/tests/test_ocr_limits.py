"""OCR resources are bounded before parsing; subprocesses die on failure."""
import importlib.util
from pathlib import Path
import sys
import types
from unittest.mock import Mock

import pytest


@pytest.fixture
def ocr(monkeypatch):
    fastmcp = types.ModuleType('mcp.server.fastmcp')
    fastmcp.FastMCP = lambda *a: types.SimpleNamespace(tool=lambda: lambda function: function)
    monkeypatch.setitem(sys.modules, 'mcp.server.fastmcp', fastmcp)
    spec = importlib.util.spec_from_file_location('ocr_under_test', Path(__file__).parents[1] / 'ocr-server' / 'server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('options', [{'dpi': 0}, {'dpi': 301}, {'dpi': True}, {'max_pages': 0}, {'max_pages': 21}, {'max_pages': True}])
def test_pdf_resource_options_refused_before_reading_file(ocr, options):
    assert ocr.extract_text_from_pdf('/not/read.pdf', **options).startswith('Error:')


def test_subprocess_output_and_time_are_bounded(ocr):
    with pytest.raises(ValueError, match='size limit'):
        ocr._bounded_output([sys.executable, '-c', 'print("x" * 10000)'], limit=10, timeout=1)
    with pytest.raises(TimeoutError):
        ocr._bounded_output([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=0.1)
    assert ocr._bounded_output([sys.executable, '-c', 'print("hello")'], timeout=1) == (0, 'hello\n')


def test_pdf_renders_and_closes_one_page_at_a_time(ocr, monkeypatch):
    active = []
    calls = []
    def render(path, **options):
        assert not active, 'previous page was retained when next rendered'
        assert options['first_page'] == options['last_page']
        assert options['timeout'] <= 60
        calls.append(options['first_page'])
        image = Mock(width=100, height=100)
        active.append(image)
        image.close.side_effect = active.clear
        return [image]
    pdf = types.ModuleType('pdf2image')
    pdf.convert_from_path = render
    pdf.pdfinfo_from_path = lambda *a, **k: {'Pages': 30}
    tess = types.ModuleType('pytesseract')
    tess.image_to_string = lambda img, **k: 'page text' if k['timeout'] <= 30 else None
    monkeypatch.setitem(sys.modules, 'pdf2image', pdf)
    monkeypatch.setitem(sys.modules, 'pytesseract', tess)
    text = ocr._ocr_pdf_pages('fake.pdf', max_pages=2)
    assert calls == [1, 2] and not active
    assert 'first 2 of 30' in text


def test_url_download_failure_does_not_expose_url_secrets(ocr, monkeypatch):
    monkeypatch.setattr(ocr, '_check_pytesseract', lambda: True)
    monkeypatch.setattr(ocr, '_check_tesseract', lambda: True)
    def fail(*a, **k):
        raise ValueError('https://user:SECRET@private/?token=SECRET')
    monkeypatch.setattr(ocr, 'download', fail)
    assert 'SECRET' not in ocr.extract_text_from_image_url('https://image.example/')


def test_native_page_cannot_hide_a_scanned_page(ocr, tmp_path, monkeypatch):
    path = tmp_path / 'mixed.pdf'
    path.write_bytes(b'fake PDF bytes')
    native = ' '.join(['preserved'] * 60)
    monkeypatch.setattr(ocr, '_try_pdftotext', lambda *a: native + '\f\f')
    monkeypatch.setattr(ocr, '_check_pdf2image', lambda: True)
    monkeypatch.setattr(ocr, '_check_pytesseract', lambda: True)
    monkeypatch.setattr(ocr, '_check_tesseract', lambda: True)
    calls = []
    def render(path, **kwargs):
        calls.append(kwargs['first_page'])
        return [Mock(width=100, height=100)]
    monkeypatch.setitem(sys.modules, 'pdf2image', types.SimpleNamespace(
        pdfinfo_from_path=lambda *a, **kw: {'Pages': 2}, convert_from_path=render))
    monkeypatch.setitem(sys.modules, 'pytesseract', types.SimpleNamespace(
        image_to_string=lambda *a, **kw: 'scanned second page'))
    result = ocr.extract_text_from_pdf(str(path))
    assert native in result and 'scanned second page' in result
    assert calls == [2]
