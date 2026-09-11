"""Org moves preserve data across partial failure, interruption and concurrency."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import org_transaction as tx
import org_workspace_adapter as adapter


@pytest.fixture
def documents(tmp_path, monkeypatch):
    monkeypatch.setattr(adapter, '_ledger_emit', lambda *a, **k: False)
    source, target = tmp_path / 'source.org', tmp_path / 'target.org'
    source.write_text('* TODO One\n  :PROPERTIES:\n  :ID: one\n  :END:\n  original body\n* TODO Two\n  :PROPERTIES:\n  :ID: two\n  :END:\n')
    target.write_text('* Destination\n  :PROPERTIES:\n  :ID: parent\n  :END:\n')
    return source, target


def move(source, target, identity='one', *extra):
    args = adapter.build_parser().parse_args(['move', '--from', str(source), '--to', str(target), '--id', identity, *extra])
    return adapter.cmd_move(args)


def test_failed_destination_restores_source_and_target(documents, monkeypatch):
    source, target = documents
    before = [p.read_bytes() for p in documents]
    write = tx.atomic_write_text
    def failure(path, text):
        if path == target:
            raise OSError('destination disk failed')
        return write(path, text)
    monkeypatch.setattr(tx, 'atomic_write_text', failure)
    with pytest.raises(OSError):
        move(source, target)
    assert [p.read_bytes() for p in documents] == before
    assert not tx.journal_path().exists()


def test_process_death_is_recovered_before_next_adapter_read(documents):
    source, target = documents
    before = [p.read_bytes() for p in documents]
    code = '''
import os,sys
from pathlib import Path
import org_transaction as tx
import org_workspace_adapter as a
write=tx.atomic_write_text
def crash(path, text):
    if path == Path(sys.argv[2]): os._exit(23)
    return write(path,text)
tx.atomic_write_text=crash
args=a.build_parser().parse_args(['move','--from',sys.argv[1],'--to',sys.argv[2],'--id','one'])
a.cmd_move(args)
'''
    result = subprocess.run([sys.executable, '-c', code, str(source), str(target)],
                            env=dict(os.environ, PYTHONPATH=str(Path(adapter.__file__).parent)), capture_output=True)
    assert result.returncode == 23, result.stderr
    assert tx.journal_path().exists()
    args = adapter.build_parser().parse_args(['show', '--file', str(source), '--id', 'one'])
    assert adapter.cmd_show(args)['id'] == 'one'
    assert [p.read_bytes() for p in documents] == before
    assert not tx.journal_path().exists()


def test_recovery_never_overwrites_an_external_edit(documents):
    source, target = documents
    transaction = tx.Transaction(tx.journal_path())
    transaction.watch(source)
    transaction.write(source, 'intermediate\n')
    source.write_text('external edit after interruption\n')
    with pytest.raises(tx.RecoveryRequired):
        tx.recover(tx.journal_path())
    assert source.read_text() == 'external edit after interruption\n'
    assert 'original body' in tx.journal_path().read_text()


def test_concurrent_moves_keep_both_tasks_exactly_once(documents):
    source, target = documents
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda identity: move(source, target, identity), ['one', 'two']))
    assert all(result.get('moved') for result in results)
    for identity in ['one', 'two']:
        assert f':ID: {identity}\n' not in source.read_text()
        assert target.read_text().count(f':ID: {identity}\n') == 1
    assert 'original body' in target.read_text()


def test_invalid_add_date_rolls_back_task_creation(documents):
    source, _ = documents
    before = source.read_bytes()
    args = adapter.build_parser().parse_args(['add', '--file', str(source), '--allow-any-file', '--heading', 'new', '--scheduled', 'not-a-date'])
    result = adapter.cmd_add(args)
    assert result.get('error')
    assert source.read_bytes() == before


def test_target_parent_cannot_come_from_source_file(documents):
    source, target = documents
    before = [p.read_bytes() for p in documents]
    result = move(source, target, 'one', '--parent-id', 'two')
    assert result.get('error')
    assert [p.read_bytes() for p in documents] == before


def test_standup_creation_uses_supported_api_and_collision_free_ids(tmp_path):
    import standup_sync
    path = tmp_path / 'org' / 'next_actions.org'
    path.parent.mkdir()
    path.write_text('* Existing\n')
    first = standup_sync.create_standup_task(str(tmp_path), 'worker', 'same task')
    second = standup_sync.create_standup_task(str(tmp_path), 'worker', 'same task')
    assert first['id'] != second['id']
    assert len(standup_sync.get_standup_tasks(str(tmp_path), 'worker')) == 2
    assert standup_sync.check_off(str(tmp_path), first['id'])['state'] == 'DONE'
    tasks = {task['id']: task['state'] for task in standup_sync.get_standup_tasks(str(tmp_path), 'worker')}
    assert tasks == {first['id']: 'DONE', second['id']: 'TODO'}


@pytest.mark.parametrize('extra', [['--heading', 'valid\n* TODO injected'], ['--property', 'BAD\n:END:=value']])
def test_structural_input_cannot_create_extra_org_nodes(documents, extra):
    source, _ = documents
    before = source.read_bytes()
    arguments = ['add', '--file', str(source), '--allow-any-file', '--heading', 'new', *extra]
    with pytest.raises(ValueError):
        adapter.cmd_add(adapter.build_parser().parse_args(arguments))
    assert source.read_bytes() == before


def test_dedup_preserves_different_bodies_and_original_identifiers(documents):
    from dedup_tasks import retire_duplicate
    source, _ = documents
    @tx.serialized
    def exercise():
        ws = tx.SafeOrgWorkspace()
        ws.load(source)
        for identity, body in [('first', 'shared body'), ('different', 'unique body'), ('duplicate', 'shared body')]:
            ws.create_node(source, 'Repeated title', state='TODO', body=body, ID=identity)
        first = ws.find_by_id('first')
        assert not retire_duplicate(ws, first, ws.find_by_id('different'))
        assert retire_duplicate(ws, first, ws.find_by_id('duplicate'))
        ws.save()
        assert ws.find_by_id('duplicate').todo == 'CANCELLED'
        assert ws.find_by_id('duplicate').get_property('DEDUPE_OF') == 'first'
        assert ws.find_by_id('different').todo == 'TODO'
    exercise()
    text = source.read_text()
    assert ':ID: duplicate' in text and 'shared body' in text and 'unique body' in text
