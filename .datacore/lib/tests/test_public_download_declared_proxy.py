"""The safe downloader goes out through a proxy only when one is DECLARED.

Owner decision 2026-11-17 (news on the chief of staff's machine): that machine
reaches the internet only through its egress proxy, and the downloader, which
ignores proxies so that nothing in the environment can redirect a download,
could fetch nothing there. "Pick whatever is more secure while it gets the job
done."

Rule:
  * a proxy is used only when DATACORE_EGRESS_PROXY names it; HTTPS_PROXY and
    the other ambient variables are still ignored;
  * the target is checked first (public addresses only), exactly as before;
  * the proxy only tunnels (CONNECT); TLS is made with the real site through
    the tunnel, so the proxy never sees or alters the content;
  * a proxy that refuses the tunnel is an error that says so.
"""
from __future__ import annotations

import socket
import ssl
import threading

import pytest

import public_download as pd

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]


class FakeProxy:
    """Accepts one connection, records the request head, answers `status`."""

    def __init__(self, status: bytes = b"200 Connection established"):
        self.status, self.head = status, b""
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            conn.settimeout(5)
            try:
                while b"\r\n\r\n" not in self.head:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    self.head += chunk
                conn.sendall(b"HTTP/1.1 " + self.status + b"\r\n\r\n")
                conn.recv(4096)          # the client's TLS hello, then hang up
            except OSError:
                pass

    def close(self):
        self.sock.close()
        self.thread.join(timeout=5)


@pytest.fixture()
def public(monkeypatch):
    monkeypatch.setattr(pd, "public_addresses", lambda host, port, timeout=None: PUBLIC)
    for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "DATACORE_EGRESS_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_a_declared_proxy_is_asked_for_a_tunnel_to_the_real_host(public, monkeypatch):
    proxy = FakeProxy()
    monkeypatch.setenv("DATACORE_EGRESS_PROXY", f"http://127.0.0.1:{proxy.port}")
    # The fake proxy is not the real site: the TLS handshake through the tunnel
    # must FAIL. That is the proof that TLS is made with the site, not the proxy.
    with pytest.raises((ssl.SSLError, OSError)):
        pd.download("https://feeds.example.org/rss.xml")
    proxy.close()
    assert proxy.head.startswith(b"CONNECT feeds.example.org:443 HTTP/1.1\r\n"), proxy.head[:80]
    assert b"rss.xml" not in proxy.head, "the proxy must see only the tunnel request, never the path"


def test_a_proxy_that_refuses_the_tunnel_is_an_error_that_says_so(public, monkeypatch):
    proxy = FakeProxy(b"403 Forbidden")
    monkeypatch.setenv("DATACORE_EGRESS_PROXY", f"http://127.0.0.1:{proxy.port}")
    with pytest.raises(OSError, match="egress proxy refused"):
        pd.download("https://feeds.example.org/rss.xml")
    proxy.close()


def test_an_ambient_proxy_variable_is_still_ignored(public, monkeypatch):
    proxy = FakeProxy()
    monkeypatch.setenv("HTTPS_PROXY", f"http://127.0.0.1:{proxy.port}")
    monkeypatch.setattr(pd.socket, "socket", _refusing_socket)
    with pytest.raises(OSError):
        pd.download("https://feeds.example.org/rss.xml")
    proxy.close()
    assert proxy.head == b"", "an undeclared proxy from the environment was contacted"


def test_a_private_target_is_refused_before_the_proxy_is_contacted(monkeypatch):
    proxy = FakeProxy()
    monkeypatch.setenv("DATACORE_EGRESS_PROXY", f"http://127.0.0.1:{proxy.port}")
    monkeypatch.setattr(pd, "resolve", lambda host, port, timeout=None: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 443))])
    with pytest.raises(ValueError, match="public addresses"):
        pd.download("https://intranet.example.org/secret")
    proxy.close()
    assert proxy.head == b""


@pytest.mark.parametrize("bad", ["socks5://127.0.0.1:1080", "http://user:pw@127.0.0.1:3128", "127.0.0.1:3128",
                                 "http://127.0.0.1"])
def test_a_malformed_declaration_is_refused_not_guessed(public, monkeypatch, bad):
    monkeypatch.setenv("DATACORE_EGRESS_PROXY", bad)
    with pytest.raises(ValueError, match="DATACORE_EGRESS_PROXY"):
        pd.download("https://feeds.example.org/rss.xml")


class _refusing_socket(socket.socket):
    def connect(self, address):
        raise ConnectionRefusedError("direct egress is closed on this machine")
