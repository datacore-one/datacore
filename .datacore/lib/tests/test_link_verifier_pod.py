"""The link check passes a live page from inside an agent pod (2026-10-27).

Data's pod reaches the internet only through its egress proxy, and Cloudflare
answers datafund.io from the box's address with a bot challenge (403,
`cf-mitigated: challenge`). The strict probe uses no proxy, so every post with
a datafund.io link failed "Verification failed" and Data posted nothing. When
the strict probe cannot decide, the check retries through the configured proxy
and counts a Cloudflare challenge as a page that exists. A 404 still fails.
"""
import io
from urllib.error import HTTPError

import link_verifier as lv

URL = "https://datafund.io/blog/how-to-give-an-ai-agent-a-record-it-cant-rewrite/"


def _http_error(code, headers):
    from email.message import Message
    m = Message()
    for k, v in headers.items():
        m[k] = v
    return HTTPError(URL, code, "x", m, io.BytesIO(b""))


def _strict_fails(monkeypatch):
    def boom(url):
        raise OSError("no route without the proxy")
    monkeypatch.setattr(lv, "probe", boom)


def test_a_cloudflare_challenge_through_the_proxy_passes(monkeypatch):
    _strict_fails(monkeypatch)
    monkeypatch.setattr(lv, "_proxied_status", lambda url, timeout: (_ for _ in ()).throw(
        _http_error(403, {"cf-mitigated": "challenge", "server": "cloudflare"})))
    ok, d = lv.LinkVerifier().verify_url(URL)
    assert ok, d


def test_a_404_through_the_proxy_still_fails(monkeypatch):
    _strict_fails(monkeypatch)
    monkeypatch.setattr(lv, "_proxied_status", lambda url, timeout: (_ for _ in ()).throw(
        _http_error(404, {"server": "cloudflare"})))
    ok, d = lv.LinkVerifier().verify_url(URL)
    assert not ok and d["status_code"] == 404


def test_a_plain_403_is_not_a_challenge(monkeypatch):
    _strict_fails(monkeypatch)
    monkeypatch.setattr(lv, "_proxied_status", lambda url, timeout: (_ for _ in ()).throw(
        _http_error(403, {"server": "nginx"})))
    ok, _ = lv.LinkVerifier().verify_url(URL)
    assert not ok


def test_a_200_page_through_the_proxy_passes(monkeypatch):
    _strict_fails(monkeypatch)
    monkeypatch.setattr(lv, "_proxied_status", lambda url, timeout: (200, "text/html; charset=utf-8"))
    ok, d = lv.LinkVerifier().verify_url(URL)
    assert ok, d


def test_a_strict_challenge_is_also_accepted(monkeypatch):
    monkeypatch.setattr(lv, "probe", lambda url: (_ for _ in ()).throw(
        _http_error(403, {"cf-mitigated": "challenge"})))
    ok, d = lv.LinkVerifier().verify_url(URL)
    assert ok, d
