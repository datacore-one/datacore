"""Sensitive POSTs face a real TLS peer, including slow headers and bodies."""
from datetime import datetime, timedelta, timezone
import socket
import socketserver
import ssl
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import pytest

import public_download as dl


@pytest.fixture
def tls_peer(tmp_path, monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'owned-test.invalid')])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('owned-test.invalid')]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                         serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    state = {'phase': 'normal', 'received': False}
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.settimeout(2)
            try:
                self.request.recv(65536)
                state['received'] = True
                if state['phase'] == 'normal':
                    self.request.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok')
                    return
                if state['phase'] == 'body':
                    self.request.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 100\r\n\r\n')
                    stream = b'x' * 100
                else:
                    stream = b'HTTP/1.0 200 OK\r\nX-Slow: ' + b'x' * 100
                for byte in stream:
                    self.request.sendall(bytes([byte]))
                    time.sleep(0.025)
            except OSError:
                pass
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        def get_request(self):
            sock, addr = super().get_request()
            try:
                return context.wrap_socket(sock, server_side=True), addr
            except Exception:
                sock.close()
                raise
    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(dl, 'public_addresses', lambda host, port, **kw: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', port))])
    try:
        yield state, server.server_address[1], cert_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def trust_fixture_certificate(monkeypatch, path):
    context = ssl.create_default_context(cafile=str(path))
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    monkeypatch.setattr(ssl, '_create_default_https_context', lambda: context)


def test_untrusted_certificate_never_receives_post_body(tls_peer):
    state, port, _ = tls_peer
    with pytest.raises(ssl.SSLCertVerificationError):
        dl.post(f'https://owned-test.invalid:{port}/rpc', b'private fixture')
    assert state['received'] is False


def test_trusted_certificate_for_wrong_host_never_receives_body(tls_peer, monkeypatch):
    state, port, cert = tls_peer
    trust_fixture_certificate(monkeypatch, cert)
    with pytest.raises(ssl.SSLCertVerificationError):
        dl.post(f'https://wrong-host.invalid:{port}/rpc', b'private fixture')
    assert state['received'] is False


@pytest.mark.parametrize('phase', ['normal', 'headers', 'body'])
def test_verified_post_has_an_absolute_deadline(tls_peer, monkeypatch, phase):
    state, port, cert = tls_peer
    state['phase'] = phase
    trust_fixture_certificate(monkeypatch, cert)
    started = time.monotonic()
    if phase == 'normal':
        assert dl.post(f'https://owned-test.invalid:{port}/rpc', b'fixture', timeout=0.3) == b'ok'
    else:
        with pytest.raises(TimeoutError):
            dl.post(f'https://owned-test.invalid:{port}/rpc', b'fixture', timeout=0.3)
    assert time.monotonic() - started < 0.9
    assert state['received'] is True
