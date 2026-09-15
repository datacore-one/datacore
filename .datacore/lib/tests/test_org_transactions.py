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


def test_independent_workspace_creation_cannot_conflate_equal_headings(tmp_path, monkeypatch):
    import org_workspace.workspace as upstream
    original = upstream.generate_id
    from datetime import datetime
    monkeypatch.setattr(upstream, 'generate_id', lambda heading, *args, **kwargs:
                        original(heading, datetime(2026, 1, 1), **kwargs))
    paths = [tmp_path / 'first.org', tmp_path / 'second.org']
    for path in paths:
        path.write_text('')
    identities = []
    @tx.serialized
    def create(path, body):
        ws = tx.SafeOrgWorkspace()
        ws.load(path)
        node = ws.create_node(path, 'Same title', state='NEXT', body=body)
        ws.save(path)
        return node.id()
    for index, path in enumerate(paths):
        identities.append(create(path, f'Independent capture {index}'))
    assert len(set(identities)) == 2
    for index, path in enumerate(paths):
        assert f'Independent capture {index}' in path.read_text()


def test_creation_cannot_reuse_an_existing_identity(tmp_path):
    path = tmp_path / 'tasks.org'
    text = '* NEXT Existing\n:PROPERTIES:\n:ID: retained\n:END:\nOriginal.\n'
    path.write_text(text)
    @tx.serialized
    def attempt():
        ws = tx.SafeOrgWorkspace()
        ws.load(path)
        ws.create_node(path, 'Different task', ID='retained')
    with pytest.raises(ValueError, match='duplicate Org IDs'):
        attempt()
    assert path.read_text() == text


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


@pytest.mark.parametrize('replacement', ['edit', 'symlink', 'new-file'])
def test_watched_but_unwritten_source_does_not_block_owned_rollback(tmp_path, monkeypatch, replacement):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    source, output = tmp_path / 'source.md', tmp_path / 'output.md'
    neighbor = tmp_path / 'neighbor.md'
    neighbor.write_text('Independent retained content\n')
    if replacement != 'new-file':
        source.write_text('Original input\n')
    output.write_text('Previous output\n')

    @tx.serialized
    def attempt():
        tx.watch_file(source)
        tx.write_org_text(output, 'Unacknowledged output\n')
        if replacement == 'symlink':
            source.unlink()
            source.symlink_to(neighbor)
        else:
            source.write_text('Independent retained content\n')
        raise ValueError('source changed before acknowledgement')

    with pytest.raises(ValueError, match='source changed'):
        attempt()
    assert output.read_text() == 'Previous output\n'
    assert source.read_text() == neighbor.read_text() == 'Independent retained content\n'
    assert source.is_symlink() is (replacement == 'symlink')
    assert not tx.journal_path().exists()


def test_change_receipts_exclude_reads_noops_and_reverted_writes(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    paths = {name: tmp_path / (name + '.md') for name in ('read', 'noop', 'revert', 'write', 'move')}
    for path in paths.values():
        path.write_text('Original\n')
    destination = tmp_path / 'archive.md'

    @tx.serialized
    def mutate():
        tx.watch_file(paths['read'])
        tx.write_org_text(paths['noop'], 'Original\n')
        tx.write_org_text(paths['revert'], 'Intermediate\n')
        tx.write_org_text(paths['revert'], 'Original\n')
        tx.write_org_text(paths['write'], 'Acknowledged\n')
        tx.move_file(paths['move'], destination)
        assert tx.changed_files() == {str(paths['write']): tx.digest('Acknowledged\n'),
                                      str(paths['move']): None,
                                      str(destination): tx.digest('Original\n')}
    mutate()


def test_historical_recovery_journal_does_not_own_unwritten_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    source, output = tmp_path / 'source.md', tmp_path / 'output.md'
    source.write_text('New independent input\n')
    output.write_text('Interrupted output\n')
    tx.atomic_write_json(tx.journal_path(), {'version': 1, 'files': {
        str(source): {'before': 'Old input\n', 'versions': [tx.digest('Old input\n')]},
        str(output): {'before': 'Old output\n', 'versions': [tx.digest('Old output\n'),
                                                            tx.digest('Interrupted output\n')]},
    }})
    tx.recover(tx.journal_path())
    assert source.read_text() == 'New independent input\n'
    assert output.read_text() == 'Old output\n'


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
@pytest.mark.parametrize('across_files', [False, True])
def test_load_cannot_silently_reassign_duplicate_identity(tmp_path, across_files):
    from org_transaction import SafeOrgWorkspace, serialized
    first = tmp_path / 'first.org'
    second = tmp_path / 'second.org'
    text = '* TODO One\n:PROPERTIES:\n:ID: shared\n:END:\n'
    first.write_text(text if across_files else text + text.replace('One', 'Two'))
    second.write_text(text.replace('One', 'Two'))
    before = {p: p.read_bytes() for p in (first, second)}
    @serialized
    def attempt():
        ws = SafeOrgWorkspace()
        ws.load(first)
        if across_files:
            ws.load(second)
        ws.set_heading(ws.find_by_id('shared'), 'Changed')
        ws.save(first)
    with pytest.raises(ValueError, match='duplicate Org IDs'):
        attempt()
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize('marker', ['<' * 7 + ' ours', '|' * 7 + ' base', '=' * 7, '>' * 7 + ' theirs', '<' * 10 + ' ours'])
def test_unresolved_org_cannot_be_reviewed_projected_or_mutated(tmp_path, monkeypatch, marker):
    from intent_sources import org_nodes
    from ledger.projection_state import snapshot
    import org_workspace_adapter as adapter
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'private-state'))
    source = '* TODO Keep authored work\n' + marker + '\n* TODO Another branch\n'
    path = tmp_path / 'inbox.org'
    path.write_text(source)
    for action in (lambda: org_nodes(tmp_path, path), lambda: snapshot(source, 'fixture'),
                   lambda: adapter.cmd_add(adapter.build_parser().parse_args(
                       ['add', '--file', str(path), '--heading', 'unrelated capture']))):
        with pytest.raises(ValueError, match='conflict'):
            action()
        assert path.read_text() == source


@pytest.mark.parametrize('body', [
    '#+begin_example\n' + '<' * 7 + ' ours\n' + '=' * 7 + '\n' + '>' * 7 + ' theirs\n#+end_example\n',
    '#+BEGIN_SRC text\n' + '<' * 7 + ' ours\n' + '=' * 7 + '\n' + '>' * 7 + ' theirs\n#+END_SRC\n',
    ': ' + '<' * 7 + ' ours\n: ' + '=' * 7 + '\n: ' + '>' * 7 + ' theirs\n',
])
def test_literal_conflict_examples_remain_readable(tmp_path, body):
    from intent_sources import org_nodes
    from ledger.projection_state import snapshot
    source = '* TODO Task\n:PROPERTIES:\n:ID: example\n:END:\n' + body
    path = tmp_path / 'inbox.org'
    path.write_text(source)
    assert len(org_nodes(tmp_path, path)) == 1
    assert 'example' in snapshot(source, 'fixture')['items']
