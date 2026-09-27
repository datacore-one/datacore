"""The Datacore Hermes plugin passes approval button presses on (owner decision 2026-09-27).

The Hermes gateway is the Winston bot's one getUpdates consumer, and its Telegram
adapter dispatches callback data by prefix: anything it does not know (our
`cosap:` Approve / Dismiss) was dropped unanswered, so the button spun and the
approval never moved. The plugin now takes `cosap:` presses before the adapter's
own handler and hands each one to the chief-of-staff press handler
(`cos_approvals_poll.py --press`), which runs the typed reply's decision path.
Everything else stays Hermes's. No real Telegram: python-telegram-bot is faked.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(LIB / "hermes_plugin"))

import hermes_plugin as hp  # noqa: E402


# ── a python-telegram-bot stand-in ──────────────────────────────────────────


def _fake_tx():
    tx = types.ModuleType("telegram.ext")

    class CallbackQueryHandler:
        def __init__(self, callback, pattern=None):
            self.callback = callback
            self.pattern = pattern

    class CommandHandler:
        def __init__(self, command, callback):
            self.callback = callback

    class Application:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler, group=0):
            self.handlers.append((group, handler))

    tx.CallbackQueryHandler = CallbackQueryHandler
    tx.CommandHandler = CommandHandler
    tx.Application = Application
    return tx


class Query:
    def __init__(self, data, *, uid=111, chat=111, text="Winston asks\n\nQueue them?"):
        self.id = "cq1"
        self.data = data
        self.from_user = types.SimpleNamespace(id=uid, first_name="P")
        self.message = types.SimpleNamespace(message_id=42, chat_id=chat,
                                             chat=types.SimpleNamespace(id=chat, type="private"),
                                             text=text)
        self.answers: list = []
        self.other: list = []

    async def answer(self, text=None, **kw):
        self.answers.append(text)

    async def edit_message_text(self, *a, **kw):  # the plugin itself must never edit
        self.other.append(("edit", a, kw))


class Adapter:
    """The shape of Hermes's TelegramAdapter wiring: a bound method as callback."""

    def __init__(self):
        self.seen: list = []

    async def _handle_callback_query(self, update, context):
        self.seen.append(update.callback_query.data)


def _wired(tx):
    app, adapter = tx.Application(), Adapter()
    app.add_handler(tx.CallbackQueryHandler(adapter._handle_callback_query))
    return app, adapter


def _update(q):
    return types.SimpleNamespace(callback_query=q)


@pytest.fixture
def tx(monkeypatch):
    fake = _fake_tx()
    assert hp.install_press_forwarder(fake) is True
    return fake


@pytest.fixture
def presses(monkeypatch):
    seen, alerts = [], []
    result = {"value": {"outcome": "decided", "answered": True}}

    def run(payload, timeout=60):
        seen.append(payload)
        v = result["value"]
        if isinstance(v, Exception):
            raise v
        return v

    monkeypatch.setattr(hp, "run_press", run)
    monkeypatch.setattr(hp, "alert_group", lambda text: alerts.append(text))
    return types.SimpleNamespace(seen=seen, alerts=alerts, result=result)


# ── routing ─────────────────────────────────────────────────────────────────


def test_an_approval_press_is_passed_on_and_hermes_never_sees_it(tx, presses):
    app, adapter = _wired(tx)
    q = Query("cosap:a:3f6c063e-aaaa")
    asyncio.run(app.handlers[0][1].callback(_update(q), None))
    assert adapter.seen == []
    assert presses.seen == [{
        "id": "cq1", "data": "cosap:a:3f6c063e-aaaa", "from": {"id": 111},
        "message": {"message_id": 42, "chat": {"id": 111}, "text": "Winston asks\n\nQueue them?"}}]
    assert q.answers == [] and q.other == [], "the press handler answered; the plugin must not answer twice"
    assert presses.alerts == []


@pytest.mark.parametrize("data", ["gt:send:abc", "ea:once:3", "update_prompt:y", "mp:x", "", None])
def test_every_other_button_stays_hermess(tx, presses, data):
    app, adapter = _wired(tx)
    asyncio.run(app.handlers[0][1].callback(_update(Query(data)), None))
    assert adapter.seen == [data]
    assert presses.seen == []


def test_other_handlers_are_left_alone_and_install_is_idempotent(presses):
    fake = _fake_tx()
    assert hp.install_press_forwarder(fake) is True
    assert hp.install_press_forwarder(fake) is True
    app, adapter = _wired(fake)
    cmd = fake.CommandHandler("start", adapter._handle_callback_query)
    app.add_handler(cmd)
    assert cmd.callback == adapter._handle_callback_query
    asyncio.run(app.handlers[0][1].callback(_update(Query("cosap:d:r1")), None))
    assert len(presses.seen) == 1, "wrapped twice: one press forwarded twice"


def test_without_python_telegram_bot_nothing_is_installed(monkeypatch):
    monkeypatch.setitem(sys.modules, "telegram", None)
    monkeypatch.setitem(sys.modules, "telegram.ext", None)
    assert hp.install_press_forwarder() is False


def test_register_installs_the_forwarder(monkeypatch):
    calls = []
    monkeypatch.setattr(hp, "install_press_forwarder", lambda *a: calls.append(a) or True)
    ctx = types.SimpleNamespace(register_hook=lambda *a: None, register_tool=lambda **k: None)
    hp.register(ctx)
    assert calls == [()]


# ── failures: the button stops spinning, the group hears, the gateway lives ──


def test_an_unrecorded_press_is_answered_and_alerted_to_the_group(tx, presses):
    presses.result["value"] = {"outcome": "error", "answered": False, "why": "script missing"}
    app, _ = _wired(tx)
    q = Query("cosap:a:r2")
    asyncio.run(app.handlers[0][1].callback(_update(q), None))
    assert len(q.answers) == 1 and "reply" in q.answers[0].lower() and "r2" in q.answers[0]
    assert q.other == [], "no message to the principal's chat — errors go to the group"
    assert len(presses.alerts) == 1 and "r2" in presses.alerts[0]


def test_an_error_the_press_handler_already_answered_is_not_answered_twice(tx, presses):
    presses.result["value"] = {"outcome": "error", "answered": True}
    app, _ = _wired(tx)
    q = Query("cosap:a:r3")
    asyncio.run(app.handlers[0][1].callback(_update(q), None))
    assert q.answers == []
    assert presses.alerts == [], "the press handler alerts its own decision errors"


def test_a_crash_in_forwarding_never_reaches_the_gateway(tx, presses):
    presses.result["value"] = RuntimeError("boom")
    app, adapter = _wired(tx)
    q = Query("cosap:d:r4")
    asyncio.run(app.handlers[0][1].callback(_update(q), None))   # must not raise
    assert adapter.seen == []
    assert len(q.answers) == 1 and len(presses.alerts) == 1


def test_a_malformed_approval_press_just_stops_spinning(tx, presses):
    presses.result["value"] = {"outcome": "ignored", "answered": False}
    app, _ = _wired(tx)
    q = Query("cosap:x:")
    asyncio.run(app.handlers[0][1].callback(_update(q), None))
    assert q.answers == [None] and presses.alerts == []


def test_a_refused_press_is_logged(tx, presses, caplog):
    presses.result["value"] = {"outcome": "unauthorised", "answered": True}
    app, _ = _wired(tx)
    with caplog.at_level(logging.WARNING, logger=hp.logger.name):
        asyncio.run(app.handlers[0][1].callback(_update(Query("cosap:a:r5", uid=999, chat=999)), None))
    assert any("refused" in r.getMessage() and "999" in r.getMessage() for r in caplog.records)


# ── run_press: the hand-off to the chief-of-staff press handler ─────────────


def _fleet(tmp_path, body: str) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "cos_approvals_poll.py").write_text(body)
    return lib


def test_run_press_hands_the_callback_to_the_press_handler_on_stdin(tmp_path, monkeypatch):
    lib = _fleet(tmp_path, (
        "import json, sys\n"
        "cq = json.loads(sys.stdin.read())\n"
        "print('noise')\n"
        "print(json.dumps({'outcome': 'decided', 'answered': True, 'args': sys.argv[1:], 'data': cq['data']}))\n"))
    monkeypatch.setenv("DATACORE_LIB", str(lib))
    out = hp.run_press({"id": "c", "data": "cosap:a:r6"})
    assert out["outcome"] == "decided" and out["args"] == ["--press"] and out["data"] == "cosap:a:r6"


def test_run_press_reports_a_missing_handler_as_an_unanswered_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LIB", str(tmp_path / "nowhere"))
    out = hp.run_press({"id": "c", "data": "cosap:a:r7"})
    assert out["outcome"] == "error" and out["answered"] is False


def test_run_press_reports_garbage_output_as_an_unanswered_error(tmp_path, monkeypatch):
    lib = _fleet(tmp_path, "import sys; sys.stdin.read(); print('Traceback: boom'); sys.exit(1)\n")
    monkeypatch.setenv("DATACORE_LIB", str(lib))
    out = hp.run_press({"id": "c", "data": "cosap:a:r8"})
    assert out["outcome"] == "error" and out["answered"] is False
