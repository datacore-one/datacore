"""Prepared write-backs preserve source data across interruption and retry."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
import org_transaction as tx
import writeback_store as store
import zettel_db


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(zettel_db, 'SPACES', {'test': {'path': tmp_path}})
    with zettel_db.get_connection('test') as conn:
        conn.executescript('''
        CREATE TABLE pending_writes (id INTEGER PRIMARY KEY, table_name TEXT, record_id INTEGER,
          operation TEXT, changes TEXT, target_file TEXT, status TEXT, error_message TEXT, applied_at TEXT);
        CREATE TABLE file_checksums (path TEXT PRIMARY KEY, checksum TEXT, indexed_at TEXT, modified_at TEXT);
        ''')
    target = tmp_path / 'source.org'
    target.write_bytes(b'* TODO Same prefix longer\r\n* TODO Same prefix\r\n:PROPERTIES:\r\n:ID: target\r\n:END:\r\noriginal\r\n')
    return tmp_path, target


def queue_append(target, content='\nnew content\n'):
    return store.queue('test', 'tasks', 1, str(target), 'append', {'content': content})


def test_queue_refuses_cross_space_or_non_content_destinations(setup):
    root, target = setup
    outside = root.parent / 'outside.md'
    outside.write_text('private')
    link = root / 'escape.md'
    link.symlink_to(outside)
    for path in (outside, link, root / 'policy.yaml'):
        with pytest.raises(ValueError, match='content space'):
            queue_append(path)
    assert outside.read_text() == 'private'


def test_complete_heading_selection_and_ambiguous_refusal(setup):
    _, target = setup
    before = target.read_bytes()
    assert store.update_file(target, 'update_state', {'heading': 'Same prefix', 'old_state': 'TODO', 'new_state': 'DONE'})[0]
    result = target.read_bytes()
    assert result == before.replace(b'* TODO Same prefix\r\n', b'* DONE Same prefix\r\n')
    duplicated = '* TODO repeated\n* TODO repeated\n'
    target.write_text(duplicated)
    assert not store.update_file(target, 'update_state', {'heading': 'repeated', 'old_state': 'TODO', 'new_state': 'DONE'})[0]
    assert target.read_text() == duplicated


def test_property_injection_and_malformed_drawers_cannot_damage_neighbors(setup):
    _, target = setup
    before = target.read_bytes()
    assert not store.update_file(target, 'update_property', {'heading': 'Same prefix', 'property': 'ID:\n* DONE forged', 'new_value': 'bad'})[0]
    assert target.read_bytes() == before
    assert store.update_file(target, 'update_property', {'heading': 'Same prefix', 'property': 'NOTE', 'new_value': 'line\n* TODO forged'})[0]
    assert b'\n* TODO forged' not in target.read_bytes()
    target.write_text('* TODO broken\n:PROPERTIES:\n:ID: x\n* TODO neighbor\n')
    before = target.read_bytes()
    assert not store.update_file(target, 'update_property', {'heading': 'broken', 'property': 'ID', 'new_value': 'bad'})[0]
    assert target.read_bytes() == before


def test_stale_queued_write_cannot_overwrite_newer_content(setup):
    _, target = setup
    identity = queue_append(target)
    target.write_text('newer external edit\n')
    assert not store.process(identity, 'test')[0]
    assert target.read_text() == 'newer external edit\n'


def test_file_failure_preserves_original_and_pending_intent(setup, monkeypatch):
    _, target = setup
    before = target.read_bytes()
    identity = queue_append(target)
    original = tx.atomic_write_text
    def fail(path, content):
        if path == target:
            raise OSError('injected failure')
        original(path, content)
    monkeypatch.setattr(tx, 'atomic_write_text', fail)
    with pytest.raises(OSError):
        store.process(identity, 'test')
    assert target.read_bytes() == before
    monkeypatch.setattr(tx, 'atomic_write_text', original)
    assert store.process(identity, 'test')[0]
    assert target.read_bytes() == before + b'\nnew content\n'


def test_concurrent_and_repeated_processing_appends_once(setup):
    _, target = setup
    before = target.read_bytes()
    identity = queue_append(target)
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(result[0] for result in pool.map(lambda _: store.process(identity, 'test'), range(8)))
    assert target.read_bytes() == before + b'\nnew content\n'


def test_process_death_after_file_commit_before_acknowledgement_is_reconciled(setup):
    root, target = setup
    before = target.read_bytes()
    identity = queue_append(target)
    script = '''
import os, sys
from pathlib import Path
import zettel_db, writeback_store
zettel_db.SPACES = {'test': {'path': Path(sys.argv[1])}}
writeback_store._finish = lambda *args: os._exit(24)
writeback_store.process(int(sys.argv[2]), 'test')
'''
    env = {**os.environ, 'PYTHONPATH': str(Path(store.__file__).parent)}
    result = subprocess.run([sys.executable, '-c', script, str(root), str(identity)], env=env, timeout=10)
    assert result.returncode == 24
    assert target.read_bytes() == before + b'\nnew content\n'
    assert store.process(identity, 'test')[0]
    assert target.read_bytes() == before + b'\nnew content\n'
    with zettel_db.get_connection('test') as conn:
        assert conn.execute('SELECT status FROM pending_writes WHERE id=?', (identity,)).fetchone()['status'] == 'completed'


def test_legacy_queue_cannot_guess_what_was_already_applied(setup):
    _, target = setup
    before = target.read_bytes()
    with zettel_db.get_connection('test') as conn:
        conn.execute("INSERT INTO pending_writes (id, operation, changes, target_file, status) VALUES (1, 'append', ?, ?, 'pending')",
                     ('{"content": "duplicate"}', str(target)))
    assert not store.process(1, 'test')[0]
    assert target.read_bytes() == before
