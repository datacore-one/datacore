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
    assert value.data == space / '.datacore/modules/acme/news/data'
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


def test_existing_separate_user_data_is_retained_without_migration(layout):
    _, space, code = layout
    parent = space / '.datacore/modules/news'
    data = parent / 'data'
    data.mkdir(parents=True, mode=0o700)
    (data / 'note').write_bytes(b'original data')
    (parent / 'settings.local.yaml').write_text('local: retained\n')
    before = (data.stat().st_ino, (data / 'note').stat().st_ino)
    assert resolve('news', code).data == data
    assert resolve('news', code, create=False).data == data
    assert before == (data.stat().st_ino, (data / 'note').stat().st_ino)
    assert (data / 'note').read_bytes() == b'original data'
    assert not (space / '.datacore/module-data').exists()


def test_already_separate_new_layout_is_not_moved_back(layout):
    _, space, code = layout
    data = space / '.datacore/module-data/news/data'
    data.mkdir(parents=True, mode=0o700)
    for parent in (data.parent, data.parent.parent):
        parent.chmod(0o700)
    (data / 'note').write_text('retained')
    assert resolve('news', code).data == data
    assert not (space / '.datacore/modules/news').exists()


def test_two_existing_stores_refuse_without_selecting_or_overwriting(layout):
    _, space, code = layout
    for directory in ('modules', 'module-data'):
        data = space / '.datacore' / directory / 'news/data'
        data.mkdir(parents=True, mode=0o700)
        (data / 'note').write_text(directory)
    with pytest.raises(ValueError, match='multiple'):
        resolve('news', code)
    for directory in ('modules', 'module-data'):
        assert (space / '.datacore' / directory / 'news/data/note').read_text() == directory


@pytest.mark.parametrize('linked', [False, True])
def test_code_installed_in_space_gets_separate_data_without_renaming_code(layout, linked):
    _, space, provider = layout
    installed = space / '.datacore/modules/news'
    installed.parent.mkdir(parents=True)
    if linked:
        installed.symlink_to(provider, target_is_directory=True)
    else:
        installed.mkdir()
    (installed / 'module.yaml').write_text('name: news\n')
    assert resolve('news', installed).data == space / '.datacore/module-data/news/data'
    assert not (installed / 'data').exists()
    assert (installed / 'module.yaml').read_text() == 'name: news\n'


def test_symlinked_existing_data_does_not_gain_compatibility_exception(layout):
    root, space, code = layout
    outside = root.parent / 'outside'
    outside.mkdir()
    parent = space / '.datacore/modules/news'
    parent.mkdir(parents=True)
    (parent / 'data').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='aliased'):
        resolve('news', code)
    assert list(outside.iterdir()) == []
