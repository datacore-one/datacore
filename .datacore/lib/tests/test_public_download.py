"""Downloads validate every destination and never re-resolve after validation."""
import socket
from unittest.mock import Mock
import pytest
import public_download as dl


def resolved(ip):
    return [(socket.AF_INET6 if ':' in ip else socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, 443))]


@pytest.mark.parametrize('ip', ['127.0.0.1', '10.0.0.1', '172.16.0.1', '192.168.0.1', '169.254.169.254', '100.64.0.1', '0.0.0.0', '224.0.0.1', '::1', '::ffff:127.0.0.1', 'fc00::1', 'fe80::1', '2002:7f00:1::'])
def test_nonpublic_networks_refused_before_connect(monkeypatch, ip):
    monkeypatch.setattr(dl, 'resolve', lambda *a, **k: resolved(ip))
    connect = Mock(side_effect=AssertionError('network must not be reached'))
    monkeypatch.setattr(dl.http.client, 'HTTPSConnection', connect)
    with pytest.raises(ValueError):
        dl.download('https://image.example/photo')
    connect.assert_not_called()


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'ftp://example.org/a', 'https://user:password@example.org/a', 'https://example.org/\r\nX: bad'])
def test_unsupported_urls_refused_without_dns(monkeypatch, url):
    lookup = Mock(side_effect=AssertionError('DNS must not be reached'))
    monkeypatch.setattr(dl, 'resolve', lookup)
    with pytest.raises(ValueError):
        dl.download(url)
    lookup.assert_not_called()


def test_mixed_public_private_dns_is_refused(monkeypatch):
    monkeypatch.setattr(dl, 'resolve', lambda *a, **k: resolved('8.8.8.8') + resolved('127.0.0.1'))
    with pytest.raises(ValueError):
        dl.public_addresses('image.example', 443)


def response(status=200, headers=None, chunks=None):
    result = Mock(status=status)
    result.getheader.side_effect = lambda key: (headers or {}).get(key)
    result.read1.side_effect = (chunks or [b'png', b''])
    return result


def transport(monkeypatch, responses):
    instances = []
    def factory(host, port, **kwargs):
        conn = Mock(sock=None)
        conn.getresponse.return_value = responses.pop(0)
        instances.append(conn)
        return conn
    monkeypatch.setattr(dl.http.client, 'HTTPSConnection', factory)
    monkeypatch.setattr(dl.http.client, 'HTTPConnection', factory)
    return instances


def test_redirect_to_internal_network_is_revalidated(monkeypatch):
    monkeypatch.setattr(dl, 'resolve', lambda host, *a, **k: resolved('127.0.0.1' if host == 'localhost' else '8.8.8.8'))
    connections = transport(monkeypatch, [response(302, {'Location': 'http://localhost/private'})])
    with pytest.raises(ValueError):
        dl.download('https://image.example/a')
    assert len(connections) == 1
    connections[0].close.assert_called_once()


def test_dns_result_is_pinned_for_actual_connection(monkeypatch):
    lookup = Mock(return_value=resolved('8.8.8.8'))
    monkeypatch.setattr(dl, 'resolve', lookup)
    connections = transport(monkeypatch, [response()])
    assert dl.download('https://image.example/a') == b'png'
    sock = Mock()
    monkeypatch.setattr(dl.socket, 'socket', lambda *a: sock)
    # Simulate DNS changing after validation: the connector uses no resolver.
    lookup.side_effect = AssertionError('unexpected second DNS lookup')
    assert connections[0]._create_connection(('image.example', 443), 1) is sock
    sock.connect.assert_called_once_with(('8.8.8.8', 443))


@pytest.mark.parametrize('reply', [response(headers={'Content-Length': '100'}), response(chunks=[b'12345', b''])])
def test_announced_and_streamed_size_are_bounded(monkeypatch, reply):
    monkeypatch.setattr(dl, 'resolve', lambda *a, **k: resolved('8.8.8.8'))
    connections = transport(monkeypatch, [reply])
    with pytest.raises(ValueError, match='size limit'):
        dl.download('https://image.example/a', max_bytes=4)
    connections[0].close.assert_called_once()


def test_truncated_download_is_not_a_success(monkeypatch):
    monkeypatch.setattr(dl, 'resolve', lambda *a, **k: resolved('8.8.8.8'))
    transport(monkeypatch, [response(headers={'Content-Length': '4'}, chunks=[b'12', b''])])
    with pytest.raises(ValueError, match='incomplete'):
        dl.download('https://image.example/a')


@pytest.mark.parametrize('destination', ['https://elsewhere.example/a', 'http://image.example/a', 'https://image.example:444/a'])
def test_redirect_cannot_forward_explicit_credentials(monkeypatch, destination):
    monkeypatch.setattr(dl, 'resolve', lambda *a, **k: resolved('8.8.8.8'))
    connections = transport(monkeypatch, [response(302, {'Location': destination}), response()])
    assert dl.download('https://image.example/a', headers={'Cookie': 'private', 'Authorization': 'Bearer private'}) == b'png'
    first = connections[0].request.call_args.kwargs['headers']
    second = connections[1].request.call_args.kwargs['headers']
    assert first['Cookie'] == 'private' and first['Authorization'] == 'Bearer private'
    assert 'Cookie' not in second and 'Authorization' not in second


def test_same_origin_redirect_retains_credentials_but_plaintext_never_sends_them(monkeypatch):
    monkeypatch.setattr(dl, 'resolve', lambda *a, **k: resolved('8.8.8.8'))
    connections = transport(monkeypatch, [response(302, {'Location': '/next'}), response()])
    assert dl.download('https://image.example/a', headers={'Cookie': 'private'}) == b'png'
    assert connections[1].request.call_args.kwargs['headers']['Cookie'] == 'private'
    with pytest.raises(ValueError, match='HTTPS'):
        dl.download('http://image.example/a', headers={'Cookie': 'private'})
