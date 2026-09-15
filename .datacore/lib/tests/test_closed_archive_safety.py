"""Closed-subtree archival must preserve both files across interruption/retry."""
from pathlib import Path
import sys

import pytest
import org_archive_closed as archive


def invoke(source, target, monkeypatch, *extra):
    monkeypatch.setattr(sys, 'argv', ['org_archive_closed.py', '--file', str(source), '--archive', str(target), *extra])
    return archive.main()


def test_source_cannot_be_its_own_archive(tmp_path, monkeypatch):
    source = tmp_path / 'inbox.org'
    before = '* DONE Preserve this result\nFull body\n* TODO Keep this task\n'
    source.write_text(before)
    try:
        invoke(source, source, monkeypatch)
    except (ValueError, OSError):
        pass
    assert source.read_text() == before


def test_archive_preserves_unmoved_crlf_bytes(tmp_path, monkeypatch):
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    source.write_bytes(b'#+TITLE: Keep header\r\n* DONE Completed\r\nBody\r\n* TODO Keep\r\nExact body\r\n')
    invoke(source, target, monkeypatch)
    assert source.read_bytes() == b'#+TITLE: Keep header\r\n* TODO Keep\r\nExact body\r\n'
    assert b'Body\r\n' in target.read_bytes()


def test_archive_never_rewrites_non_heading_star_lines(tmp_path, monkeypatch):
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    source.write_text('* DONE Complete\n#+BEGIN_SRC text\n*literal-data\n**another-literal\n#+END_SRC\n* TODO Keep\n')
    invoke(source, target, monkeypatch)
    text = target.read_text()
    assert '\n*literal-data\n**another-literal\n' in text


def test_nonterminal_custom_child_prevents_parent_archival(tmp_path, monkeypatch):
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    before = '#+TODO: TODO READY BLOCKED | DONE CANCELLED\n* DONE Parent\n** READY Still needed\n'
    source.write_text(before)
    invoke(source, target, monkeypatch)
    assert source.read_text() == before and not target.exists()


def test_inherited_private_tag_is_preserved_in_archive(tmp_path, monkeypatch):
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    source.write_text('#+FILETAGS: :private:\n* Group :protected:\n** DONE Complete :local:\nOriginal body\n* TODO Keep\n')
    invoke(source, target, monkeypatch)
    text = target.read_text()
    assert ':private:' in text or ':private' in text
    assert 'protected' in text and 'local' in text and 'Original body' in text


def test_failed_second_publication_restores_both_files(tmp_path, monkeypatch):
    import org_transaction as tx
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    before = '* DONE Complete\nOriginal body\n* TODO Keep\n'
    source.write_text(before);target.write_text('* Historical notes\nPreserve\n')
    old_target = target.read_bytes()
    original = tx.atomic_write_text
    failed = False
    def fail(path, text):
        nonlocal failed
        original(path, text)
        if path == source and not failed:
            failed = True
            raise OSError('synthetic fsync failure')
    monkeypatch.setattr(tx, 'atomic_write_text', fail)
    with pytest.raises(OSError, match='synthetic fsync failure'):
        archive.archive_closed(source, target)
    assert source.read_text() == before and target.read_bytes() == old_target


def test_stale_source_edit_is_preserved_and_archive_rolled_back(tmp_path, monkeypatch):
    import org_transaction as tx
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    before = '* DONE Complete\nOriginal body\n* TODO Keep\n'
    source.write_text(before);target.write_text('* Historical notes\n')
    original = tx.atomic_write_text
    changed = False
    def concurrent_edit(path, text):
        nonlocal changed
        original(path, text)
        if path == target and not changed:
            changed = True
            source.write_text(before + '* TODO Concurrent valid task\n')
    monkeypatch.setattr(tx, 'atomic_write_text', concurrent_edit)
    with pytest.raises(tx.RecoveryRequired):
        archive.archive_closed(source, target)
    assert source.read_text() == before + '* TODO Concurrent valid task\n'
    assert target.read_text() == '* Historical notes\n'


def test_competing_archives_preserve_one_copy(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    source.write_text('* DONE Complete\n:PROPERTIES:\n:ID: unique-fixture\n:END:\nOriginal body\n* TODO Keep\n')
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: archive.archive_closed(source, target), range(8)))
    assert sum(r['archived'] for r in results) == 1
    assert target.read_text().count(':ID: unique-fixture') == 1
    assert source.read_text() == '* TODO Keep\n'


def test_crash_after_archive_write_recovers_before_retry(tmp_path):
    import os
    import subprocess
    import org_transaction as tx
    source, target = tmp_path / 'inbox.org', tmp_path / 'archive.org'
    source.write_text('* DONE Complete\n:PROPERTIES:\n:ID: unique-fixture\n:END:\nOriginal body\n* TODO Keep\n')
    target.write_text('* Previous archive\n')
    script = '''
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import org_archive_closed as a
import org_transaction as tx
original=tx.atomic_write_text
def crash(path,text):
 original(path,text)
 if path==Path(sys.argv[3]):os._exit(47)
tx.atomic_write_text=crash
a.archive_closed(Path(sys.argv[2]),Path(sys.argv[3]))
'''
    child = subprocess.run([sys.executable, '-c', script, str(Path(archive.__file__).parent), str(source), str(target)],
                           env=dict(os.environ), capture_output=True, text=True, timeout=10)
    assert child.returncode == 47, child.stderr
    assert tx.journal_path().exists()
    assert archive.archive_closed(source, target)['archived'] == 1
    assert target.read_text().startswith('* Previous archive\n')
    assert target.read_text().count(':ID: unique-fixture') == 1
    assert not tx.journal_path().exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_linked_archive_paths_refuse_without_modifying_data(tmp_path, kind):
    source, target, victim = tmp_path / 'inbox.org', tmp_path / 'archive.org', tmp_path / 'victim'
    source.write_text('* DONE Complete\nOriginal body\n')
    victim.write_text('preserve victim')
    if kind == 'symlink': target.symlink_to(victim)
    else: target.hardlink_to(victim)
    with pytest.raises(ValueError): archive.archive_closed(source, target)
    assert source.read_text() == '* DONE Complete\nOriginal body\n'
    assert victim.read_text() == 'preserve victim'
