import json
import os
from pathlib import Path

import pytest

from module_context import parse_json, read_text, resolve


@pytest.fixture
def layout(tmp_path, monkeypatch):
    root = tmp_path / 'root'
    space = root / 'nested/personal'
    (space / '.datacore').mkdir(parents=True)
    (space / '.datacore/config.yaml').write_text('space: {name: mine, type: personal}\n')
    code = tmp_path / 'provider'
    code.mkdir()
    monkeypatch.setenv('DATACORE_ROOT', str(root))
    monkeypatch.delenv('DATACORE_SPACE', raising=False)
    return root, space, code


def test_private_context_binds_canonical_identity_and_keeps_code_unchanged(layout):
    root, space, code = layout
    value = resolve('acme/news', code)
    assert value.name == 'mine'
    assert value.space == space
    assert value.data == space / '.datacore/module-data/acme/news/data'
    (value.data / 'retained').write_text('private')
    assert list(code.iterdir()) == []
    assert value.data.stat().st_mode & 0o077 == 0
    assert resolve('acme/news', code).data == value.data


def test_read_only_resolution_does_not_create_an_empty_store(layout):
    _, space, code = layout
    value = resolve('news', code, create=False)
    assert not value.data.exists()
    assert not (space / '.datacore/module-data').exists()


@pytest.mark.parametrize('kind', ['missing', 'duplicate', 'team-personal-ordinal'])
def test_ambiguous_or_team_identity_cannot_become_personal_default(layout, kind):
    root, space, code = layout
    if kind == 'duplicate':
        second = root / 'second/.datacore'
        second.mkdir(parents=True)
        (second / 'config.yaml').write_text('space: {name: also-personal, type: personal}\n')
    else:
        (space / '.datacore/config.yaml').write_text('space: {name: mine, type: team}\n')
        if kind == 'team-personal-ordinal':
            space.rename(root / '0-personal')
    with pytest.raises(ValueError, match='ambiguous'):
        resolve('news', code)


@pytest.mark.parametrize('component', ['data', 'state', 'settings.local.yaml'])
def test_legacy_components_require_preserved_migration(layout, component):
    _, space, code = layout
    (code / component).write_text('preserve')
    with pytest.raises(ValueError, match='migration'):
        resolve('news', code)
    assert (code / component).read_text() == 'preserve'
    assert not (space / '.datacore/module-data').exists()


@pytest.mark.parametrize('field,value', [('status', 'staged'), ('space', 'foreign'), ('module', 'other'), ('version', True)])
def test_incomplete_or_foreign_receipt_cannot_enable_data(layout, field, value):
    _, _, code = layout
    selected = resolve('news', code)
    selected.data.rmdir()
    record = dict(version=1, status='complete', module='news', space='mine')
    record[field] = value
    (selected.data.parent / '.migration.json').write_text(json.dumps(record))
    with pytest.raises(ValueError, match='migration'):
        resolve('news', code)
    assert not selected.data.exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'oversize', 'utf8'])
def test_bounded_reads_reject_unsafe_inputs(tmp_path, kind):
    path = tmp_path / 'input'
    other = tmp_path / 'original'
    other.write_text('preserve')
    if kind == 'symlink':
        path.symlink_to(other)
    elif kind == 'hardlink':
        os.link(other, path)
    elif kind == 'fifo':
        os.mkfifo(path)
    else:
        path.write_bytes(b'x' * 101 if kind == 'oversize' else b'\xff')
    with pytest.raises((ValueError, OSError)):
        read_text(tmp_path, path, limit=100)
    assert other.read_text() == 'preserve'


def test_short_reads_preserve_complete_unicode_and_duplicate_json_is_refused(tmp_path, monkeypatch):
    path = tmp_path / 'input'
    path.write_text('€🙂' * 100)
    original = os.read
    monkeypatch.setattr(os, 'read', lambda fd, size: original(fd, min(size, 7)))
    assert read_text(tmp_path, path) == '€🙂' * 100
    with pytest.raises(ValueError):
        parse_json('{"items": [1], "items": []}')
    with pytest.raises(ValueError):
        parse_json('{"score": NaN}')
