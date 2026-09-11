"""Cloud speech cannot bypass TLS, consent, bounds or atomic publication."""
import base64
import json
import socket
import ssl
import stat
import sys
import types

import pytest
import public_download as dl

import speech_transport as speech


def encoded(audio=b'audio'):
    return json.dumps([['wrb.fr', 'jQ1olc', json.dumps([
        base64.b64encode(audio).decode('ascii')])]]).encode()


class Response:
    status = 200
    def __init__(self, content=None):
        self.content = [encoded()] if content is None else content
    def getheader(self, name):
        return None
    def read1(self, limit):
        if not self.content:
            return b''
        chunk = self.content.pop(0)
        if len(chunk) > limit:
            self.content.insert(0, chunk[limit:])
        return chunk[:limit]


@pytest.fixture
def transport(monkeypatch):
    class Transport:
        def __init__(self):
            self.calls = []
            self.responses = [Response()]
    session = Transport()
    class Connection:
        sock = None
        def __init__(self, host, port, **kwargs):
            assert host == 'translate.google.com' and port == 443
            self.response = session.responses.pop(0)
        def request(self, method, path, **kwargs):
            session.calls.append((method, path, kwargs))
        def getresponse(self):
            if isinstance(self.response, Exception):
                raise self.response
            return self.response
        def close(self):
            pass
    monkeypatch.setattr(dl.http.client, 'HTTPSConnection', Connection)
    monkeypatch.setattr(dl, 'public_addresses', lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('8.8.8.8', 443))])
    class Formatter:
        def __init__(self, **kwargs):
            assert kwargs['lang_check'] is False
        def get_bodies(self):
            return ['fixture request'] * len(session.responses)
        def save(self, *a):
            pytest.fail('gTTS unsafe transport must not be called')
    monkeypatch.setitem(sys.modules, 'gtts', types.SimpleNamespace(gTTS=Formatter))
    return session


def test_verified_request_and_private_atomic_output(transport, tmp_path):
    target = tmp_path / 'brief.mp3'
    target.write_bytes(b'previous')
    assert speech.synthesize_google('approved', target, allow_cloud=True) == target
    assert target.read_bytes() == b'audio'
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    method, path, options = transport.calls[0]
    assert method == 'POST' and path == '/_/TranslateWebserverUi/data/batchexecute'
    assert options['body'] == b'fixture request'
    assert options['headers']['Accept-Encoding'] == 'identity'
    assert not list(tmp_path.glob('.datacore-speech-*'))


@pytest.mark.parametrize('response', [
    ssl.SSLCertVerificationError('fixture private text'),
    TimeoutError('fixture private text'),
    Response([b'invalid']), Response([b'\xff']),
    Response([b'[["wrb.fr", "jQ1olc", "[null]"]]']),
    Response([b'[["wrb.fr", "jQ1olc", "[\\"!!\\"]"]]']),
])
def test_partial_synthesis_failure_preserves_existing_audio(transport, tmp_path, response):
    target = tmp_path / 'brief.mp3'
    target.write_bytes(b'previous complete audio')
    transport.responses = [Response(), response]
    with pytest.raises(RuntimeError) as failure:
        speech.synthesize_google('approved', target, allow_cloud=True)
    assert 'private text' not in str(failure.value)
    assert target.read_bytes() == b'previous complete audio'
    assert not list(tmp_path.glob('.datacore-speech-*'))


@pytest.mark.parametrize('status', [301, 302, 307, 400, 403, 500])
def test_redirects_and_failed_statuses_cannot_publish(transport, tmp_path, status):
    response = Response()
    response.status = status
    transport.responses = [response]
    target = tmp_path / 'brief.mp3'
    with pytest.raises(RuntimeError, match='HTTPS request failed'):
        speech.synthesize_google('approved', target, allow_cloud=True)
    assert not target.exists() and not list(tmp_path.iterdir())


def test_consent_and_input_checks_precede_network_or_files(transport, tmp_path):
    target = tmp_path / 'brief.mp3'
    for consent in (False, None, 1, 'true'):
        with pytest.raises(RuntimeError, match='disabled'):
            speech.synthesize_google('private', target, allow_cloud=consent)
    for value in ('', ' ', None, 'x' * (speech.MAX_TEXT + 1)):
        with pytest.raises(ValueError):
            speech.synthesize_google(value, target, allow_cloud=True)
    assert not transport.calls and not list(tmp_path.iterdir())


@pytest.mark.parametrize('limit', ['response', 'audio', 'time', 'publication'])
def test_limits_and_publication_failure_preserve_audio(transport, tmp_path, monkeypatch, limit):
    target = tmp_path / 'brief.mp3'
    target.write_bytes(b'previous')
    if limit == 'response':
        monkeypatch.setattr(speech, 'MAX_RESPONSE', 8)
    elif limit == 'audio':
        monkeypatch.setattr(speech, 'MAX_AUDIO', 2)
    elif limit == 'time':
        times = iter([0, 121])
        monkeypatch.setattr(speech, 'time', types.SimpleNamespace(monotonic=lambda: next(times)))
    else:
        def denied(*a):
            raise OSError('simulated disk failure')
        monkeypatch.setattr(speech.os, 'replace', denied)
    with pytest.raises((RuntimeError, OSError)):
        speech.synthesize_google('approved', target, allow_cloud=True)
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('.datacore-speech-*'))
