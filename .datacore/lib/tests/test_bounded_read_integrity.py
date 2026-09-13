"""Attack the descriptor-bound snapshot, including intermediate-directory races."""
import os

import pytest

from file_utils import read_text_within


def test_directory_swap_cannot_redirect_read_outside_root(tmp_path, monkeypatch):
    root = tmp_path / 'data'
    inside = root / 'nested'
    outside = tmp_path / 'outside'
    inside.mkdir(parents=True)
    outside.mkdir()
    (inside / 'record').write_text('permitted')
    (outside / 'record').write_text('must never be read')
    original_open, original_read = os.open, os.read
    read_bytes = []
    switched = False
    def racing_open(path, flags, *a, **kw):
        nonlocal switched
        if str(path) == 'record' and not switched:
            switched = True
            inside.rename(root / 'retained')
            inside.symlink_to(outside, target_is_directory=True)
        return original_open(path, flags, *a, **kw)
    def recording_read(fd, size):
        result = original_read(fd, size)
        read_bytes.append(result)
        return result
    monkeypatch.setattr(os, 'open', racing_open)
    monkeypatch.setattr(os, 'read', recording_read)
    with pytest.raises((OSError, ValueError)):
        read_text_within(root, inside / 'record')
    assert switched
    assert b'must never be read' not in b''.join(read_bytes)
    assert (root / 'retained/record').read_text() == 'permitted'


@pytest.mark.parametrize('kind', ['replace', 'inplace', 'truncate'])
def test_changed_file_cannot_acknowledge_a_snapshot(tmp_path, monkeypatch, kind):
    path = tmp_path / 'record'
    path.write_text('before')
    original = os.read
    changed = False
    def read(fd, size):
        nonlocal changed
        data = original(fd, size)
        if not changed:
            changed = True
            if kind == 'replace':
                other = tmp_path / 'replacement'
                other.write_text('newer!')
                other.replace(path)
            else:
                path.write_text('newer!' if kind == 'inplace' else '')
        return data
    monkeypatch.setattr(os, 'read', read)
    with pytest.raises(ValueError):
        read_text_within(tmp_path, path)
    assert path.read_text() == ('' if kind == 'truncate' else 'newer!')
