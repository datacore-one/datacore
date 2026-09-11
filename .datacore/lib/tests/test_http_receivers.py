"""Exercise real loopback HTTP request paths with disposable stores."""
import hashlib
import hmac
from http.client import HTTPConnection, HTTPException
from http.server import HTTPServer
import json
from pathlib import Path
import sys
from threading import Thread

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import agent_stream_relay as relay
import gitea_pull_webhook as webhook
import ledger_transport
from http_utils import BoundedHTTPServer


@pytest.fixture
def receiver(tmp_path, monkeypatch):
    servers = []
    monkeypatch.setattr(relay, "EVENT_LOG_DIR", tmp_path / "events")
    monkeypatch.setattr(relay, "TOKEN_FILE", tmp_path / "token")
    monkeypatch.setattr(webhook, "DATA_DIR", tmp_path)
    monkeypatch.setattr(webhook, "ALLOWED_SPACES", {"0-test"}, raising=False)
    (tmp_path / "0-test" / ".git").mkdir(parents=True)
    calls = []
    monkeypatch.setattr(ledger_transport, "sync_repo", lambda *a, **k: calls.append(a) or "clean")

    def start(kind, secret="test-secret"):
        if kind == "relay":
            monkeypatch.setattr(relay.RelayHandler, "token", secret)
            handler = relay.RelayHandler
        else:
            monkeypatch.setattr(webhook, "SECRET", secret)
            handler = webhook.Handler
        server = HTTPServer(("127.0.0.1", 0), handler)
        server.timeout = 1
        thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        servers.append((server, thread))
        return server.server_address[1]

    yield start, calls
    for server, thread in servers:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(port, path, body=b"{}", headers=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("POST", path, body, headers=headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.mark.parametrize("kind,path", [("relay", "/events"), ("webhook", "/pull/0-test")])
def test_empty_secret_denies_mutation(receiver, kind, path):
    start, calls = receiver
    status, _ = request(start(kind, secret=""), path, b'{"type":"test","agent":"test","summary":"test"}')
    assert status == 503
    assert not calls


def test_gitea_hmac_authenticates_exact_body(receiver):
    start, calls = receiver
    port = start("webhook")
    body = b'{"event":"push"}'
    signature = hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    assert request(port, "/pull/0-test", body, {"X-Gitea-Signature": signature})[0] == 200
    assert len(calls) == 1
    assert request(port, "/pull/0-test", body + b" ", {"X-Gitea-Signature": signature})[0] == 401
    assert len(calls) == 1


def test_query_token_is_rejected_and_never_logged(receiver, capsys):
    start, calls = receiver
    status, _ = request(start("webhook"), "/pull/0-test?token=test-secret")
    assert status in (400, 401)
    assert not calls
    assert "test-secret" not in capsys.readouterr().err


def test_webhook_offline_is_not_success(receiver, monkeypatch):
    start, _ = receiver
    monkeypatch.setattr(ledger_transport, "sync_repo", lambda *a, **k: "offline")
    status, _ = request(start("webhook"), "/pull/0-test", headers={"Authorization": "Bearer test-secret"})
    assert status == 503


def test_webhook_refuses_space_symlink_escape(receiver, tmp_path):
    start, calls = receiver
    (tmp_path / "0-test" / ".git").rmdir()
    (tmp_path / "0-test").rmdir()
    (tmp_path / "0-test").symlink_to(tmp_path.parent, target_is_directory=True)
    assert request(start("webhook"), "/pull/0-test", headers={"Authorization": "Bearer test-secret"})[0] == 400
    assert not calls


def test_relay_rejects_entire_malformed_batch(receiver, tmp_path):
    start, _ = receiver
    body = json.dumps([{"id": "one", "type": "test", "agent": "test", "summary": "saved?"}, 7]).encode()
    status, _ = request(start("relay"), "/events", body, {"Authorization": "Bearer test-secret"})
    assert status == 400
    assert not list(tmp_path.rglob("*.jsonl"))


def test_relay_retry_does_not_duplicate_events(receiver, tmp_path):
    start, _ = receiver
    port = start("relay")
    body = b'{"id":"stable","type":"test","agent":"test","summary":"once"}'
    for _ in range(2):
        assert request(port, "/events", body, {"Authorization": "Bearer test-secret"})[0] == 200
    assert len(next(tmp_path.rglob("*.jsonl")).read_text().splitlines()) == 1


@pytest.mark.parametrize("field,value", [("severity", {}), ("id", []), ("ts", 42), ("agent", None)])
def test_relay_malformed_field_returns_400(receiver, field, value):
    start, _ = receiver
    body = {"type": "test", "agent": "test", "summary": "test", field: value}
    assert request(start("relay"), "/events", json.dumps(body).encode(), {"Authorization": "Bearer test-secret"})[0] == 400


def test_existing_empty_token_fails_closed(receiver, tmp_path):
    (tmp_path / "token").write_text("\n")
    with pytest.raises(ValueError, match="empty"):
        relay._ensure_token()


def test_bounded_server_limits_wait_and_workers():
    server = BoundedHTTPServer(("127.0.0.1", 0), relay.RelayHandler, max_workers=1)
    try:
        assert 0 < server.request_timeout <= 10
        assert server._slots.acquire(blocking=False)
        assert not server._slots.acquire(blocking=False)
        server._slots.release()
    finally:
        server.server_close()


def test_slow_client_deadline_releases_worker():
    import socket
    import time
    server = BoundedHTTPServer(('127.0.0.1', 0), relay.RelayHandler, max_workers=1)
    server.request_timeout = 0.2
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    connection = socket.create_connection(server.server_address, timeout=1)
    try:
        connection.sendall(b'GET /health HTTP/1.0\r\nX-Slow: ')
        started = time.monotonic()
        closed = False
        while time.monotonic() - started < 1:
            try:
                connection.sendall(b'x')
                connection.settimeout(0.05)
                if connection.recv(1) == b'':
                    closed = True
                    break
            except socket.timeout:
                continue
            except OSError:
                closed = True
                break
        assert closed, 'trickling bytes kept a worker occupied indefinitely'
        # A subsequent request can acquire the same sole worker.
        available = False
        until = time.monotonic() + 1
        while time.monotonic() < until:
            health = HTTPConnection(*server.server_address, timeout=0.2)
            try:
                health.request('GET', '/health')
                available = health.getresponse().status == 200
                if available:
                    break
            except (OSError, HTTPException):
                time.sleep(0.01)
            finally:
                health.close()
        assert available, 'worker did not recover after disconnect'
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_conflicting_retry_rejects_whole_batch(receiver, tmp_path):
    start, _ = receiver
    port = start('relay')
    headers = {'Authorization': 'Bearer test-secret'}
    row = {'id': 'one', 'type': 'test', 'agent': 'test', 'summary': 'original'}
    assert request(port, '/events', json.dumps(row).encode(), headers)[0] == 200
    path = next(tmp_path.rglob('*.jsonl'))
    before = path.read_bytes()
    batch = [dict(row, id='new'), dict(row, summary='replacement')]
    assert request(port, '/events', json.dumps(batch).encode(), headers)[0] == 409
    assert path.read_bytes() == before
