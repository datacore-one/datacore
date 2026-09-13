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


def test_no_clobber_publish_preserves_existing_file(tmp_path):
    from file_utils import atomic_write_text_within
    target = tmp_path / 'report.md'
    atomic_write_text_within(tmp_path, target, 'original', overwrite=False)
    with pytest.raises(FileExistsError):
        atomic_write_text_within(tmp_path, target, 'replacement', overwrite=False)
    assert target.read_text() == 'original'
    assert target.stat().st_nlink == 1
    assert [p.name for p in tmp_path.iterdir()] == ['report.md']


def test_no_clobber_racing_publications_have_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from file_utils import atomic_write_text_within
    target = tmp_path / 'report.md'
    def publish(value):
        try:
            atomic_write_text_within(tmp_path, target, value, overwrite=False)
            return value
        except FileExistsError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ['first', 'second']))
    winners = [value for value in results if value is not None]
    assert len(winners) == 1 and target.read_text() == winners[0]
    assert target.stat().st_nlink == 1


def test_no_clobber_parent_swap_cannot_redirect_publication(tmp_path, monkeypatch):
    root, outside = tmp_path / 'root', tmp_path / 'outside'
    inside = root / 'inside'
    inside.mkdir(parents=True)
    outside.mkdir()
    link = os.link
    def swap(*args, **kwargs):
        inside.rename(root / 'retained')
        inside.symlink_to(outside, target_is_directory=True)
        return link(*args, **kwargs)
    monkeypatch.setattr(os, 'link', swap)
    with pytest.raises(ValueError):
        atomic_write_text_within(root, inside / 'report.md', 'complete result', overwrite=False)
    assert list(outside.iterdir()) == []
    assert (root / 'retained/report.md').read_text() == 'complete result'


def test_no_clobber_competitor_during_temporary_link_window(tmp_path, monkeypatch):
    target = tmp_path / 'record'
    original = os.link
    observed = []
    def link(*args, **kwargs):
        original(*args, **kwargs)
        assert target.stat().st_nlink == 2
        # The first writer pauses after publishing its complete file but before
        # removing its temporary name. A second writer must refuse creation.
        with pytest.raises(FileExistsError):
            atomic_write_text_within(tmp_path, target, 'competitor', overwrite=False)
        observed.append(target.read_text())
    monkeypatch.setattr(os, 'link', link)
    atomic_write_text_within(tmp_path, target, 'retained winner', overwrite=False)
    assert observed == ['retained winner']
    assert target.read_text() == 'retained winner' and target.stat().st_nlink == 1


def test_no_clobber_interrupted_publication_retains_both_names(tmp_path):
    temporary = tmp_path / '.interrupted.tmp'
    temporary.write_text('complete but unacknowledged')
    target = tmp_path / 'record'
    os.link(temporary, target)
    with pytest.raises(FileExistsError):
        atomic_write_text_within(tmp_path, target, 'replacement', overwrite=False)
    assert target.read_bytes() == temporary.read_bytes() == b'complete but unacknowledged'
    assert target.stat().st_nlink == 2


@pytest.mark.parametrize('overwrite', [True, False])
def test_removed_destination_cannot_be_acknowledged_after_directory_flush(tmp_path, monkeypatch, overwrite):
    import stat
    target = tmp_path / 'record'
    original = os.fsync
    def flush(fd):
        original(fd)
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            target.unlink()
    monkeypatch.setattr(os, 'fsync', flush)
    with pytest.raises(FileNotFoundError):
        atomic_write_text_within(tmp_path, target, 'complete', overwrite=overwrite)
    assert not target.exists()
