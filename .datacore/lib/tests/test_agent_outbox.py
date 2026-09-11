"""A relay outage and inbound rsync must not erase unacknowledged events."""
import shutil
from unittest.mock import Mock, MagicMock

import pytest
import agent_emit as emitter
import agent_outbox as outbox


def test_relay_failure_is_preserved_outside_the_synced_directory(tmp_path, monkeypatch):
    canonical = tmp_path / 'agent-stream'
    canonical.mkdir()
    monkeypatch.setattr(emitter, 'EVENT_LOG_DIR', canonical)
    monkeypatch.setattr(emitter, '_RELAY_URL', 'https://relay.example.test')
    monkeypatch.setattr(emitter, '_post_to_relay', lambda row: False)
    event = emitter.emit('test', 'test', 'retained', event_id='one')
    assert event['id'] == 'one'
    shutil.rmtree(canonical)  # simulate complete replacement by inbound sync
    received = []
    monkeypatch.setattr(emitter, '_post_to_relay', lambda row: received.append(row) or True)
    assert emitter.flush_pending() == 1
    assert received == [event]
    assert not list(emitter._outbox_dir().glob('*.json'))


def test_failed_acknowledgement_retries_without_losing_the_event(tmp_path, monkeypatch):
    event = {'id': 'one', 'summary': 'data', 'ts': 'first'}
    outbox.enqueue(tmp_path, event)
    acknowledge = outbox.acknowledge
    def fail(*args):
        raise OSError('interrupted acknowledgement')
    monkeypatch.setattr(outbox, 'acknowledge', fail)
    send = Mock(return_value=True)
    with pytest.raises(OSError):
        outbox.flush(tmp_path, send)
    assert outbox.event_path(tmp_path, event).exists()
    monkeypatch.setattr(outbox, 'acknowledge', acknowledge)
    assert outbox.flush(tmp_path, send) == 1
    assert send.call_count == 2


def test_conflicting_retry_cannot_replace_pending_content(tmp_path):
    event = {'id': 'one', 'summary': 'original'}
    outbox.enqueue(tmp_path, event)
    with pytest.raises(outbox.EventConflict):
        outbox.enqueue(tmp_path, {**event, 'summary': 'different'})
    assert 'original' in outbox.event_path(tmp_path, event).read_text()


def test_http_success_without_a_relay_acknowledgement_is_not_delivery(monkeypatch):
    import urllib.request
    monkeypatch.setattr(emitter, '_RELAY_URL', 'https://relay.example.test')
    response = Mock(status=200)
    response.read.return_value = b'{"ok":false}'
    opener = MagicMock()
    opener.open.return_value.__enter__.return_value = response
    captured = []
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: captured.extend(handlers) or opener)
    assert not emitter._post_to_relay({'id': 'one'})
    assert captured[0].redirect_request(None, None, None, None, None, None) is None


def test_message_identity_includes_conversation_and_agent(monkeypatch):
    import agent_emit
    monkeypatch.setattr(agent_emit, 'emit', lambda **kwargs: kwargs)
    def message(agent='one', recipient='chat-a'):
        return agent_emit.emit_message(agent, 'telegram', 'hello', recipient=recipient, message_id='1')['event_id']
    assert message() == message()
    assert len({message(), message(recipient='chat-b'), message(agent='two')}) == 3
    # Missing scope cannot safely turn a per-chat ID into a global receipt.
    assert message(recipient=None) is None
