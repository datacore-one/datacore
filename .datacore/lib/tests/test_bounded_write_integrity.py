"""Publication cannot follow aliases or acknowledge an interrupted write."""
import os

import pytest

from file_utils import atomic_write_text_within


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'parent-symlink'])
def test_alias_cannot_redirect_publication(tmp_path, kind):
    root, outside = tmp_path / 'data', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'record').write_text('preserve')
    target = root / 'nested/record'
    if kind == 'parent-symlink':
        target.parent.symlink_to(outside, target_is_directory=True)
    else:
        target.parent.mkdir()
        if kind == 'symlink':
            target.symlink_to(outside / 'record')
        else:
            os.link(outside / 'record', target)
    with pytest.raises((ValueError, OSError)):
        atomic_write_text_within(root, target, 'new')
    assert (outside / 'record').read_text() == 'preserve'


def test_parent_swap_cannot_write_external_directory(tmp_path, monkeypatch):
    root, outside = tmp_path / 'data', tmp_path / 'outside'
    inside = root / 'nested'
    inside.mkdir(parents=True)
    outside.mkdir()
    (inside / 'record').write_text('inside')
    (outside / 'record').write_text('outside')
    original = os.replace
    def replace(*args, **kwargs):
        inside.rename(root / 'retained')
        inside.symlink_to(outside, target_is_directory=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(os, 'replace', replace)
    with pytest.raises(ValueError):
        atomic_write_text_within(root, inside / 'record', 'new')
    assert (outside / 'record').read_text() == 'outside'
    assert (root / 'retained/record').read_text() == 'new'


def test_failed_flush_preserves_previous_file_and_short_writes_finish(tmp_path, monkeypatch):
    path = tmp_path / 'record'
    path.write_text('old')
    original_fsync, original_write = os.fsync, os.write
    def fail(fd):
        raise OSError('injected persistence failure')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError):
        atomic_write_text_within(tmp_path, path, 'new')
    assert path.read_text() == 'old'
    assert list(tmp_path.iterdir()) == [path]
    monkeypatch.setattr(os, 'fsync', original_fsync)
    monkeypatch.setattr(os, 'write', lambda fd, data: original_write(fd, data[:1]))
    atomic_write_text_within(tmp_path, path, 'évidence')
    assert path.read_text() == 'évidence'
