"""One sender of alerts to the owner's group (audit 2026-11-15, step 3 "one of each").

Five scripts each carried their own copy of "read the token and the group,
shorten, POST, record what could not be delivered". The copies had drifted:
different timeouts, one with a formatter fallback, one through curl. A rule
about alerts (only the group, one screen, every failure recorded) had to be
changed in five places and was checked in five places.

`tg_format.deliver` is the one. Nothing else in the system library posts a
message to Telegram.
"""
from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

import tg_format

LIB = Path(__file__).resolve().parents[1]
#: May name the endpoint without sending a message through it.
NOT_SENDERS = {
    "tg_format.py",            # the one sender
    "credential_access.py",    # getMe liveness probes only
}


def test_nothing_else_in_the_system_library_posts_a_message():
    offenders = []
    for path in sorted(LIB.rglob("*")):
        rel = path.relative_to(LIB)
        if path.suffix not in (".py", ".sh") or not path.is_file():
            continue
        if rel.parts[0] in ("tests", "sim") or "venv" in rel.parts or rel.name in NOT_SENDERS:
            continue
        if "/sendMessage" in path.read_text(encoding="utf-8", errors="replace"):
            offenders.append(str(rel))
    assert offenders == [], f"these post to Telegram themselves instead of tg_format.deliver: {offenders}"


class _Answer:
    def __init__(self, status=200, body=b'{"ok":true,"result":{"message_id":77}}'):
        self.status, self._body = status, body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Capture every request; records land in tmp."""
    monkeypatch.setenv("DATACORE_UNDELIVERED_LOG", str(tmp_path / "undelivered.jsonl"))
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path))
    sent = []

    def fake(url, data=None, timeout=None):
        sent.append({"url": url, "data": dict(p.split("=", 1) for p in data.decode().split("&")), "timeout": timeout})
        if isinstance(fake.answer, Exception):
            raise fake.answer
        return fake.answer

    fake.answer = _Answer()
    monkeypatch.setattr(tg_format.urllib.request, "urlopen", fake)
    fake.sent = sent
    fake.undelivered = lambda: [json.loads(l) for l in (tmp_path / "undelivered.jsonl").read_text().splitlines()] \
        if (tmp_path / "undelivered.jsonl").exists() else []
    fake.sent_log = tmp_path / "sent-alerts.log"
    return fake


def test_a_delivered_message_is_one_post_to_the_group_and_says_its_id(wire):
    ok, why = tg_format.deliver("probe", "hello there", "TOKEN", "-100")
    assert ok and "77" in why
    assert len(wire.sent) == 1 and wire.sent[0]["url"].endswith("/botTOKEN/sendMessage")
    assert wire.sent[0]["data"]["chat_id"] == "-100"
    assert wire.undelivered() == []


def test_no_group_means_nothing_is_posted_and_the_alert_is_recorded(wire):
    ok, why = tg_format.deliver("probe", "hello", "TOKEN", "")
    assert not ok and "only to The Firm group" in why
    assert wire.sent == [], "never a fallback to another chat"
    assert [u["sender"] for u in wire.undelivered()] == ["probe"]


def test_no_token_is_recorded(wire):
    ok, why = tg_format.deliver("probe", "hello", "", "-100")
    assert not ok and "no bot token" in why and wire.sent == []
    assert len(wire.undelivered()) == 1


def test_a_refusal_is_recorded_with_the_status(wire):
    wire.answer = urllib.error.HTTPError("u", 401, "Unauthorized", {}, io.BytesIO(b""))
    ok, why = tg_format.deliver("probe", "hello", "TOKEN", "-100")
    assert not ok and why == "http 401"
    assert wire.undelivered()[0]["reason"] == "http 401"


def test_an_unreachable_network_is_recorded_and_the_token_is_not_in_the_reason(wire):
    wire.answer = urllib.error.URLError("no route to api.telegram.org/botTOKEN")
    ok, why = tg_format.deliver("probe", "hello", "TOKEN", "-100")
    assert not ok and "TOKEN" not in why and "URLError" in why
    assert "TOKEN" not in json.dumps(wire.undelivered())


def test_a_long_message_is_cut_to_one_screen_unless_the_caller_formatted_it(wire):
    long = "\n".join(f"line {i} of the report" for i in range(200))
    tg_format.deliver("probe", long, "TOKEN", "-100")
    tg_format.deliver("probe", long, "TOKEN", "-100", preformatted=True)
    import urllib.parse
    first, second = (urllib.parse.unquote_plus(s["data"]["text"]) for s in wire.sent)
    assert len(first.splitlines()) <= tg_format.MAX_LINES and len(first) <= tg_format.MAX_CHARS
    assert second == long


def test_html_is_escaped_and_marked_and_the_sent_log_is_kept_when_asked(wire):
    tg_format.deliver("probe", "a < b", "TOKEN", "-100", html=True, log_sent=True)
    import urllib.parse
    assert wire.sent[0]["data"]["parse_mode"] == "HTML"
    assert urllib.parse.unquote_plus(wire.sent[0]["data"]["text"]) == "a &lt; b"
    assert "sender=probe" in wire.sent_log.read_text() and "a < b" in wire.sent_log.read_text()
