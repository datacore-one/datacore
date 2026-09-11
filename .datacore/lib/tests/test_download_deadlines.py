"""Real slow peers and a stuck resolver cannot retain download workers."""
import socket
import socketserver
import threading
import time

import pytest

import bounded_dns
import public_download as dl


def test_stuck_resolver_is_terminated_and_reaped(monkeypatch, tmp_path):
    marker = tmp_path / 'resolver-survived'
    monkeypatch.setattr(bounded_dns, '_RESOLVE',
                        'import time, pathlib, sys; time.sleep(1); pathlib.Path(sys.argv[1]).touch()')
    start = time.monotonic()
    with pytest.raises(TimeoutError, match='DNS deadline'):
        bounded_dns.resolve(str(marker), 443, timeout=0.15)
    assert time.monotonic() - start < 0.8
    time.sleep(1.05)
    assert not marker.exists()


def test_native_resolver_and_numeric_addresses():
    import ipaddress
    rows = bounded_dns.resolve('localhost', 443, timeout=3)
    assert rows and all(ipaddress.ip_address(row[4][0]).is_loopback for row in rows)
    assert bounded_dns.resolve('::1', 443, timeout=1)[0][4] == ('::1', 443, 0, 0)


@pytest.mark.parametrize('phase', ['headers', 'body', 'metadata'])
def test_slow_peer_cannot_extend_absolute_deadline(monkeypatch, phase):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:
                self.request.recv(8192)
                if phase == 'body':
                    self.request.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\n')
                    stream = b'x' * 100
                else:
                    stream = b'HTTP/1.0 200 OK\r\nX-Slow: ' + b'x' * 40 + b'\r\nContent-Length: 0\r\n\r\n'
                for byte in stream:
                    self.request.sendall(bytes([byte]))
                    time.sleep(0.025)
            except OSError:
                pass
    server = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Destination-validation attacks are covered separately. This fixture
    # deliberately routes only to a disposable local adversarial peer.
    monkeypatch.setattr(dl, 'public_addresses', lambda host, port, **kw:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))])
    monkeypatch.setattr(dl, 'MAX_SECONDS', 0.2)
    try:
        start = time.monotonic()
        with pytest.raises((OSError, ValueError)):
            operation = dl.probe if phase == 'metadata' else dl.download
            operation(f'http://owned-test.invalid:{server.server_address[1]}/slow')
        assert time.monotonic() - start < 0.8
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
