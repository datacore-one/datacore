"""Archival is a data-preserving, retryable write of a complete task."""
from ledger.fold import ItemState
import ledger_done_report as archive_module
import pytest


def completed_item():
    item = ItemState('task-one', 'Archived result', 'worker', 'verified', payload={
        'level': 2, 'tags': ['work'], 'scheduled': '<2023-11-10 Fri>',
        'deadline': '<2023-11-15 Wed>',
        'org': {'body': 'Irreplaceable result\nSecond line.',
                'properties': {'SOURCE': 'local-evidence'}, 'priority': 'A'},
    })
    item.closed_at = '1700000000000.0000.writer'
    return item


def test_archive_preserves_full_content(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [completed_item()])
    space = tmp_path / '9-fixture'
    space.mkdir()
    assert archive_module.archive(space, 1) == (1, 0)
    text = next((space / '4-archive/done').glob('*.org')).read_text()
    assert 'Irreplaceable result\nSecond line.' in text
    assert ':SOURCE: local-evidence' in text
    assert '[#A]' in text
    assert 'SCHEDULED: <2023-11-10 Fri>' in text
    assert 'DEADLINE: <2023-11-15 Wed>' in text
    assert archive_module.archive(space, 1) == (0, 1)


def test_archive_does_not_treat_body_example_id_as_archived_task(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [completed_item()])
    space = tmp_path / '9-fixture'
    dest = space / '4-archive/done/2023-11.org'
    dest.parent.mkdir(parents=True)
    dest.write_text('* Notes\n#+BEGIN_SRC text\n:ID: task-one\n#+END_SRC\n')
    assert archive_module.archive(space, 1) == (1, 0)
    assert 'Archived result' in dest.read_text()


def test_archive_retains_inherited_tags_and_null_optional_fields(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    item = completed_item()
    item.payload.update(org=None, effective_tags=['team', 'work'], filetags=['gtd'])
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [item])
    space = tmp_path / '9-fixture'
    space.mkdir()
    assert archive_module.archive(space, 1) == (1, 0)
    text = next((space / '4-archive/done').glob('*.org')).read_text()
    assert ':gtd:team:work:' in text


def test_archive_keeps_agent_completion_pending_human_review(tmp_path, monkeypatch):
    item = completed_item()
    item.status = 'completed'
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [item])
    assert archive_module.archive(tmp_path, 1) == (0, 0)
    assert not (tmp_path / '4-archive').exists()


def test_conflicting_existing_entry_prevents_all_month_writes(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    first, second = completed_item(), completed_item()
    second.id = 'task-two'
    second.closed_at = '1702000000000.0000.writer'
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [first, second])
    space = tmp_path / '9-fixture'
    dest = space / '4-archive/done/2023-12.org'
    dest.parent.mkdir(parents=True)
    before = '* DONE Authored correction\n:PROPERTIES:\n:ID: task-two\n:END:\n'
    dest.write_text(before)
    with pytest.raises(archive_module.ArchiveConflict, match='different content'):
        archive_module.archive(space, 1)
    assert dest.read_text() == before
    assert not (dest.parent / '2023-11.org').exists()


def test_failed_durability_after_replacement_rolls_back_and_retry_succeeds(tmp_path, monkeypatch):
    import org_transaction as tx
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [completed_item()])
    space = tmp_path / '9-fixture'
    dest = space / '4-archive/done/2023-11.org'
    dest.parent.mkdir(parents=True)
    before = '#+TITLE: Existing authored notes\n'
    dest.write_text(before)
    original = tx.atomic_write_text
    failed = False
    def fail_once(path, text):
        nonlocal failed
        original(path, text)
        if path == dest and not failed:
            failed = True
            raise OSError('injected directory flush failure')
    monkeypatch.setattr(tx, 'atomic_write_text', fail_once)
    with pytest.raises(OSError, match='flush failure'):
        archive_module.archive(space, 1)
    assert dest.read_text() == before
    assert archive_module.archive(space, 1) == (1, 0)
    assert archive_module.archive(space, 1) == (0, 1)


@pytest.mark.parametrize('link_kind', ['file', 'directory', 'hardlink'])
def test_archive_refuses_linked_paths_without_modifying_target(tmp_path, monkeypatch, link_kind):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [completed_item()])
    space = tmp_path / '9-fixture'
    space.mkdir()
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    victim = elsewhere / '2023-11.org'
    victim.write_text('Keep these bytes\n')
    dest = space / '4-archive/done/2023-11.org'
    if link_kind == 'directory':
        dest.parent.parent.mkdir()
        dest.parent.symlink_to(elsewhere, target_is_directory=True)
    else:
        dest.parent.mkdir(parents=True)
        if link_kind == 'file':
            dest.symlink_to(victim)
        else:
            dest.hardlink_to(victim)
    with pytest.raises(archive_module.ArchiveConflict):
        archive_module.archive(space, 1)
    assert victim.read_text() == 'Keep these bytes\n'


def test_concurrent_archive_retries_write_one_copy(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [completed_item()])
    space = tmp_path / '9-fixture'
    space.mkdir()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: archive_module.archive(space, 1), range(8)))
    assert sum(result[0] for result in results) == 1
    text = next((space / '4-archive/done').glob('*.org')).read_text()
    assert text.count(':ID: task-one') == 1
    assert 'Irreplaceable result' in text


def test_process_death_during_archive_recovers_before_retry(tmp_path, monkeypatch):
    import subprocess
    import sys
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    space = tmp_path / '9-fixture'
    dest = space / '4-archive/done/2023-11.org'
    dest.parent.mkdir(parents=True)
    before = '#+TITLE: Preserved pre-crash archive\n'
    dest.write_text(before)
    script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
from test_ledger_done_archive import completed_item
import ledger_done_report as report
import org_transaction as tx
report.closed_items = lambda space: [completed_item()]
original = tx.atomic_write_text
def crash(path, text):
    original(path, text)
    if path.name == '2023-11.org':
        os._exit(47)
tx.atomic_write_text = crash
report.archive(Path(sys.argv[3]), 1)
'''
    from pathlib import Path
    test_path = Path(__file__).parent
    child = subprocess.run([sys.executable, '-c', script, str(test_path.parent),
                            str(test_path), str(space)], capture_output=True, text=True, timeout=10)
    assert child.returncode == 47, child.stderr
    assert (tmp_path / 'state/org-transaction.json').exists()
    assert 'Irreplaceable result' in dest.read_text()
    # The next invocation must recover first, then perform one complete write.
    monkeypatch.setattr(archive_module, 'closed_items', lambda space: [completed_item()])
    assert archive_module.archive(space, 1) == (1, 0)
    assert archive_module.archive(space, 1) == (0, 1)
    assert dest.read_text().startswith(before)
    assert dest.read_text().count(':ID: task-one') == 1
    assert not (tmp_path / 'state/org-transaction.json').exists()
