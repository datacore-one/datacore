"""End-to-end entry points share denial and credential transport invariants."""
import importlib.util
import io
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest
import public_download
import secret_http
from http_utils import BoundedHTTPServer


def test_authenticated_redirect_cannot_reach_second_origin_or_reflect_secrets(monkeypatch):
    request = urllib.request.Request('https://api.example/key-SYNTHETIC', headers={'Authorization': 'Bearer SYNTHETIC'})
    def open_request(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 302, 'SYNTHETIC', {'Location':'https://other.test/SYNTHETIC'}, io.BytesIO(b'SYNTHETIC'))
    captured=[]
    monkeypatch.setattr(urllib.request, 'build_opener', lambda *handlers: captured.extend(handlers) or Mock(open=open_request))
    with pytest.raises(urllib.error.HTTPError) as caught:
        secret_http.urlopen(request)
    assert captured[0].redirect_request(request, None, 302, '', {}, 'https://other.test/') is None
    assert captured[1].proxies == {}
    assert caught.value.code == 302
    assert 'SYNTHETIC' not in str(caught.value) + caught.value.geturl() + str(caught.value.headers) + caught.value.read().decode()


def test_authenticated_response_bound_applies_across_reads():
    raw=io.BytesIO(b'12345')
    response=secret_http.BoundedResponse(raw); response.remaining=4
    assert response.read(2) == b'12'
    with pytest.raises(ValueError, match='size limit'):
        response.read()
    assert raw.closed


@pytest.mark.parametrize('ip', ['127.0.0.1', '169.254.169.254', '::1'])
def test_social_link_entry_point_cannot_probe_private_network(monkeypatch, ip):
    import socket
    from link_verifier import LinkVerifier
    monkeypatch.setattr(public_download, 'resolve', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip,443))])
    connection=Mock(side_effect=AssertionError('private network accessed'))
    monkeypatch.setattr(public_download.http.client, 'HTTPSConnection', connection)
    passed, details=LinkVerifier().verify_url('https://site.example/a')
    assert not passed and details['status_code'] is None
    connection.assert_not_called()


def test_voice_daemon_auth_missing_duplicate_and_errors(monkeypatch):
    monkeypatch.setenv("DAEMON_AUTH_TOKEN", "synthetic-initial")
    path=Path(__file__).resolve().parents[2]/'modules/voice-terminal/lib/daemon_health.py'
    spec=importlib.util.spec_from_file_location('audit_daemon_health',path)
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'AUTH_TOKEN', '')
    monkeypatch.setattr(module, '_load_telegram_token', lambda: 'SYNTHETIC-TOKEN')
    monkeypatch.setenv('TELEGRAM_CHAT_ID','synthetic-chat')
    def fail(*a, **kw):
        raise ValueError('SYNTHETIC-TOKEN private provider detail')
    monkeypatch.setattr(module, 'secret_urlopen', fail)
    server=BoundedHTTPServer(('127.0.0.1',0),module.HealthHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    import http.client
    def request(method,path,headers=()):
        client=http.client.HTTPConnection(*server.server_address,timeout=3)
        client.putrequest(method,path)
        for key,value in headers: client.putheader(key,value)
        client.putheader('Content-Length','0'); client.endheaders()
        response=client.getresponse(); result=(response.status,dict(response.getheaders()),response.read());client.close();return result
    try:
        assert request('GET','/daemon/status')[0] == 401
        status,headers,body=request('GET','/daemon/health')
        assert status == 200 and 'telegram_configured' not in json.loads(body)
        assert 'Access-Control-Allow-Origin' not in headers
        monkeypatch.setattr(module,'AUTH_TOKEN','synthetic-auth')
        assert request('GET','/daemon/status',[('Authorization','Bearer synthetic-auth')]*2)[0] == 401
        status,_,body=request('POST','/daemon/test-telegram',[('Authorization','Bearer synthetic-auth')])
        assert status == 502 and b'SYNTHETIC-TOKEN' not in body
    finally:
        server.shutdown(); server.server_close();thread.join(3)


def test_actor_resolution_failure_is_an_executor_result(monkeypatch):
    import executors.base as base
    class NeverInvoked(base.Executor):
        name='test'
        def _invoke(self,*a): raise AssertionError('must not execute')
    monkeypatch.delenv('DATACORE_ACTOR',raising=False)
    monkeypatch.setattr(base,'_default_actor',lambda: (_ for _ in ()).throw(ValueError('identity missing')))
    result=NeverInvoked().run('task')
    assert 'identity missing' in result.error


def test_public_metadata_probe_falls_back_to_get_without_reading_body(monkeypatch):
    import socket
    monkeypatch.setattr(public_download,'resolve',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
    head=Mock(status=405)
    response=Mock(status=200)
    response.getheader.side_effect=lambda key,default=None: {'Content-Type':'video/mp4'}.get(key,default)
    connection=Mock();connection.getresponse.side_effect=[head,response]
    monkeypatch.setattr(public_download.http.client,'HTTPSConnection',lambda *a,**k:connection)
    assert public_download.probe('https://example.test/movie') == (200,'video/mp4','https://example.test/movie')
    assert [call.args[0] for call in connection.request.call_args_list] == ['HEAD','GET']
    response.read.assert_not_called();response.read1.assert_not_called()


@pytest.mark.parametrize('url',['http://example.test/api','http://localhost/api','http://127.0.0.2/api'])
def test_plaintext_api_opt_in_is_limited_to_explicit_loopback_literals(url):
    with pytest.raises(ValueError,match='HTTPS'):
        secret_http.urlopen(url,allow_loopback=True)


def test_malformed_authorization_header_does_not_leak_its_value():
    request=urllib.request.Request('https://example.invalid/',headers={'Authorization':'Bearer SYNTHETIC-PRIVATE\ninvalid'})
    with pytest.raises(ValueError) as caught:
        secret_http.urlopen(request,timeout=1)
    assert 'SYNTHETIC-PRIVATE' not in str(caught.value)
