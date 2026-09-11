"""Catalog duplicate labels require equal complete text, even on hash collision."""
import importlib.util
from pathlib import Path
import sys
import types


def test_catalog_digest_collision_cannot_label_different_content_duplicate(tmp_path,monkeypatch):
    # Parser behavior is independent of duplicate classification. Supply
    # deterministic extraction results without optional native packages.
    pptx=types.ModuleType('pptx');pptx.Presentation=object
    exc=types.ModuleType('pptx.exc');exc.PackageNotFoundError=ValueError
    monkeypatch.setitem(sys.modules,'pptx',pptx);monkeypatch.setitem(sys.modules,'pptx.exc',exc)
    monkeypatch.setitem(sys.modules,'pdfplumber',types.ModuleType('pdfplumber'))
    spec=importlib.util.spec_from_file_location('audit_slide_catalog',Path(__file__).resolve().parents[1]/'slide_cataloger.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    source=tmp_path/'presentations';source.mkdir()
    for name in ['a.pptx','b.pptx','c.pdf']:(source/name).write_bytes(b'synthetic')
    def extract(path):
        return {'full_content':'different' if path.stem == 'b' else 'same', 'audience':'test','slide_count':1,'slides':[]}
    monkeypatch.setattr(module,'extract_pptx',extract);monkeypatch.setattr(module,'extract_pdf',extract)
    monkeypatch.setattr(module,'content_hash',lambda _: 'colliding-digest')
    presentations=module.build_catalog(source,tmp_path)['presentations']
    assert 'duplicate_of' not in presentations[0] and 'duplicate_of' not in presentations[1]
    assert presentations[2]['duplicate_of'] == presentations[0]['id']
