import importlib
import json
import pytest


@pytest.fixture
def tail(tmp_path, monkeypatch):
    monkeypatch.setenv('MODE', 'hermes')
    import agent_stream_tail
    tail = importlib.reload(agent_stream_tail)
    monkeypatch.setattr(tail, 'STATE_FILE', tmp_path / 'state.json')
    monkeypatch.setattr(tail, '_send', lambda row: False)
    return tail


def test_outage_and_source_rotation_cannot_erase_unacknowledged_rows(tail, tmp_path):
    source = tmp_path / 'session.jsonl'
    source.write_text(json.dumps({'role': 'assistant', 'content': 'preserve this', 'timestamp': '2026-09-10T00:00:00Z'}) + '\n')
    state, dedup = {}, set()
    assert tail.tail_file(source, state, dedup) == 1
    assert state['offset'] == source.stat().st_size
    source.unlink()
    rows = [json.loads(path.read_text()) for path in tail._outbox().glob('*.json')]
    assert len(rows) == 1 and rows[0]['summary'] == 'preserve this'
    sent = []
    assert tail.flush(tail._outbox(), lambda row: sent.append(row) or True) == 1
    assert sent == rows and not list(tail._outbox().glob('*.json'))


def test_failed_durable_enqueue_cannot_advance_cursor_or_receipt(tail, tmp_path, monkeypatch):
    source = tmp_path / 'session.jsonl'
    source.write_text(json.dumps({'role': 'assistant', 'content': 'preserve'}) + '\n')
    def fail(*args):
        raise OSError('disk full')
    monkeypatch.setattr(tail, 'enqueue', fail)
    state, dedup = {}, set()
    assert tail.tail_file(source, state, dedup) == 0
    assert state == {} and dedup == set()


def test_receipts_are_stable_and_distinguish_repeated_lines(tail):
    line = b'{"role":"assistant","content":"same"}'
    first = tail.parse_hermes(line, '/session.jsonl', 0)
    second = tail.parse_hermes(line, '/session.jsonl', len(line) + 1)
    assert first['id'] != second['id']
    assert first == tail.parse_hermes(line, '/session.jsonl', 0)


def test_malformed_checkpoint_fails_closed(tail):
    tail.STATE_FILE.write_text('{')
    with pytest.raises(ValueError):
        tail.load_state()
    assert tail.STATE_FILE.read_text() == '{'
