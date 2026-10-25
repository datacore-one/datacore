"""secret_http may reach an HTTPS API through a CONNECT proxy, never otherwise.

An agent in a pod has one way out: the egress proxy. Ignoring proxies made
every keyed call fail there ("API connection failed"). Through a CONNECT
tunnel the proxy sees only the host and port; the key travels inside TLS.
Plain-HTTP calls and NO_PROXY hosts still never touch a proxy.
"""
import socket
import threading
import urllib.error
import urllib.request

import pytest

import secret_http


def _recording_proxy():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    seen = []

    def serve():
        conn, _ = srv.accept()
        conn.settimeout(3)
        try:
            seen.append(conn.recv(65536))
        finally:
            conn.close()
            srv.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv.getsockname()[1], seen, t


def _clear_proxy_env(monkeypatch):
    for k in ("http_proxy", "https_proxy", "no_proxy", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(k, raising=False)


def test_https_call_goes_through_the_proxy_as_a_tunnel_and_the_key_stays_inside(monkeypatch):
    _clear_proxy_env(monkeypatch)
    port, seen, t = _recording_proxy()
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{port}")
    req = urllib.request.Request("https://api.example.invalid/v1/chat",
                                 headers={"Authorization": "Bearer SYNTHETIC-KEY"})
    with pytest.raises(urllib.error.URLError):
        secret_http.urlopen(req, timeout=3)
    t.join(3)
    assert seen and seen[0].startswith(b"CONNECT api.example.invalid:443 HTTP/1.1")
    assert b"SYNTHETIC-KEY" not in seen[0] and b"Authorization" not in seen[0]


def _handlers(monkeypatch):
    captured = []
    monkeypatch.setattr(urllib.request, "build_opener",
                        lambda *h: captured.extend(h) or type("O", (), {"open": lambda *a, **k: None})())
    return captured


def test_plain_http_never_uses_a_proxy(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    captured = _handlers(monkeypatch)
    secret_http.urlopen("http://127.0.0.1:9/api", allow_loopback=True, timeout=1)
    assert captured[1].proxies == {}


def test_no_proxy_hosts_go_direct(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("NO_PROXY", "api.example.invalid")
    captured = _handlers(monkeypatch)
    secret_http.urlopen("https://api.example.invalid/v1", timeout=1)
    assert captured[1].proxies == {}


def test_only_the_https_proxy_is_handed_over(monkeypatch):
    _clear_proxy_env(monkeypatch)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")
    captured = _handlers(monkeypatch)
    secret_http.urlopen("https://api.example.invalid/v1", timeout=1)
    assert captured[1].proxies == {"https": "http://proxy.invalid:3128"}
