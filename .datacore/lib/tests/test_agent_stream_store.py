"""Durability, whole-batch failure, bounded storage and concurrent retries."""
from concurrent.futures import ThreadPoolExecutor
import json

import pytest
import agent_stream_store as store


def row(number):
    return {'id': str(number), 'summary': str(number), 'ts': 'original'}


def test_concurrent_batches_preserve_all_events_and_retry_identity(tmp_path):
    path = tmp_path / 'events.jsonl'
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: store.append_events(path, [row(i)]), list(range(40)) * 2))
    entries = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(entries) == 40
    assert {r['id'] for r in entries} == {str(i) for i in range(40)}


def test_store_failure_preserves_previous_batch(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    store.append_events(path, [row(1)])
    before = path.read_bytes()
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(store, 'atomic_write_text', fail)
    with pytest.raises(OSError):
        store.append_events(path, [row(2), row(3)])
    assert path.read_bytes() == before


def test_capacity_refusal_never_truncates_existing_events(tmp_path, monkeypatch):
    path = tmp_path / 'events.jsonl'
    store.append_events(path, [row(1)])
    before = path.read_bytes()
    monkeypatch.setattr(store, 'MAX_LOG_BYTES', len(before) + 1)
    with pytest.raises(ValueError, match='capacity'):
        store.append_events(path, [row(2)])
    assert path.read_bytes() == before
    assert store.append_events(path, [dict(row(1), ts='retry')]) == 0


def test_conflicting_ids_within_one_batch_do_not_partially_commit(tmp_path):
    path = tmp_path / 'events.jsonl'
    with pytest.raises(store.EventConflict):
        store.append_events(path, [row(1), dict(row(1), summary='different')])
    assert not path.exists()


def test_date_rotation_does_not_allow_retry_duplication_or_conflicts(tmp_path):
    first, second = tmp_path / 'events-2026-09-10.jsonl', tmp_path / 'events-2026-09-11.jsonl'
    store.append_events(first, [row(1)])
    assert store.append_events(second, [dict(row(1), ts='next day')]) == 0
    assert not second.exists()
    with pytest.raises(store.EventConflict):
        store.append_events(second, [row(2), dict(row(1), summary='changed')])
    assert not second.exists()
    assert len(first.read_text().splitlines()) == 1


def test_concurrent_date_files_share_one_retry_namespace(tmp_path):
    def append(day):
        return store.append_events(tmp_path / f'events-2026-09-{day:02}.jsonl', [row(1)])
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(append, range(1, 9))) == 1
