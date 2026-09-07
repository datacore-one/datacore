"""The Datacore plugin for Hermes: identity in memory, authorship gate, policy.

2026-09-06/07: the geo cadence on hermes followed a skill saying
`EventLog(actor='winston', ...)`, wrote into 5-plur's winston log, and the
verifier reported "5-plur/winston by tris" the next morning — twice, after
the fact. These tests pin the gate that refuses it before the call runs, and
the two states in which the gate deliberately stands down."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
sys.path.insert(0, str(LIB / "hermes_plugin"))

import hermes_plugin as hp  # noqa: E402

PRINCIPALS = {"winston": "winston", "bridge": "winston", "tris": "tris",
              "miles": "miles", "nightshift": "miles", "mac": "gregor"}

TRIS = {"actor": "tris", "principal": "tris", "display": "Tris",
        "role": "chief intelligence officer", "permission_mode": "yolo",
        "ok": True, "why": ""}


@pytest.fixture()
def as_tris(monkeypatch):
    """This host is Tris, and the registry knows the fleet's writers."""
    monkeypatch.setattr(hp, "_IDENTITY", dict(TRIS))
    monkeypatch.setattr(hp, "_lib", lambda: True)
    import types
    fake = types.ModuleType("actor_identity")
    fake.principal_of = lambda a, path=None: (PRINCIPALS.get(str(a).lower()), {})
    fake.this_actor = lambda strict=False: "tris"
    monkeypatch.setitem(sys.modules, "actor_identity", fake)
    return TRIS


# ── authorship gate ─────────────────────────────────────────────────────────

WRITE = ("python3 -c \"from ledger.log import EventLog; "
         "EventLog(space_dir=Path('~/Data/2-plur'), actor='winston', sign=False)"
         ".append('item.create', payload)\"")


def test_the_incident_call_is_refused_and_the_message_says_what_to_do(as_tris):
    reason = hp.foreign_actor_write(WRITE)
    assert reason and "actor 'winston'" in reason and "principal 'winston'" in reason
    assert "You are 'tris'" in reason and "actor='tris'" in reason
    out = hp.pre_tool_call("execute_code", {"code": WRITE})
    assert out == {"action": "block", "message": reason}


def test_every_shape_a_wrong_actor_takes_is_caught(as_tris):
    for text in (
        "EventLog(space_dir=d, actor=\"winston\")",
        "EventLog(space, 'winston')  # positional, from ledger.log",
        "python3 ledger_ingest_org.py --actor winston --root ~/Data",
        "DATACORE_ACTOR=winston python3 -c 'from ledger.log import EventLog'",
    ):
        assert hp.foreign_actor_write(text), text


def test_our_own_actor_and_our_own_alias_pass(as_tris):
    assert hp.foreign_actor_write("EventLog(space_dir=d, actor='tris')") is None
    assert hp.pre_tool_call("execute_code", {"code": "EventLog(d, 'tris')"}) is None


def test_an_undeclared_writer_is_left_alone(as_tris):
    # A hostname-derived log from before DIP-0044. Refusing it would break
    # more than it protects; the registry cannot decide it.
    assert hp.foreign_actor_write("EventLog(space_dir=d, actor='transporter')") is None


def test_prose_about_another_principal_is_not_a_write(as_tris):
    assert hp.foreign_actor_write("ask winston to approve the invoice") is None
    assert hp.foreign_actor_write("actor='winston' is what the old skill said") is None


def test_an_unresolved_identity_refuses_nothing(monkeypatch):
    monkeypatch.setattr(hp, "_IDENTITY", {"actor": "", "principal": "", "display": "",
                                          "role": "", "permission_mode": "",
                                          "ok": False, "why": "no registry"})
    assert hp.foreign_actor_write(WRITE) is None
    assert hp.pre_tool_call("execute_code", {"code": WRITE}) is None


def test_a_broken_guard_never_blocks_the_agent(as_tris, monkeypatch):
    monkeypatch.setattr(hp, "call_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hp.pre_tool_call("terminal", {"command": WRITE}) is None


# ── identity in memory ──────────────────────────────────────────────────────

def test_identity_block_names_the_principal_and_the_one_log(as_tris):
    b = hp.identity_block()
    assert "**Tris**, chief intelligence officer" in b
    assert "principal `tris`" in b and "actor `tris`" in b
    assert "events/tris.jsonl" in b and "NO other principal's log" in b


def test_memory_sync_is_idempotent_and_keeps_the_rest_of_the_file(tmp_path, as_tris):
    m = tmp_path / "MEMORY.md"
    m.write_text("# Memory\n\n- Gregor prefers short answers.\n")
    assert hp.sync_memory_block(m) == "added"
    assert hp.sync_memory_block(m) == "unchanged"
    text = m.read_text()
    assert text.count(hp.MARK_START) == 1
    assert "Gregor prefers short answers." in text

    stale = text.replace("actor `tris`", "actor `winston`")
    m.write_text(stale)
    assert hp.sync_memory_block(m) == "updated"
    assert "actor `winston`" not in m.read_text()
    assert m.read_text().count(hp.MARK_START) == 1
    assert "Gregor prefers short answers." in m.read_text()


def test_memory_sync_does_nothing_without_an_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(hp, "_IDENTITY", {"ok": False, "why": "x", "actor": "",
                                          "principal": "", "display": "", "role": "",
                                          "permission_mode": ""})
    m = tmp_path / "MEMORY.md"
    assert hp.sync_memory_block(m) == "skipped"
    assert not m.exists()


# ── fleet policy reaches Hermes tool names ──────────────────────────────────

def test_hermes_acting_tools_are_classified_like_claude_code_ones():
    import tool_policy as tp
    effects = tp.load_effects()
    pay = "curl -s https://api.stripe.com/v1/charges -d amount=500"
    for tool in ("terminal", "execute_code", "Bash"):
        assert "payment" in tp.classify(tool, {"command": pay}, effects), tool
    assert tp.classify("read_file", {"path": "/etc/hosts"}, effects) == set()


def test_a_refused_effect_becomes_a_block_message(as_tris, monkeypatch):
    import types
    fake = types.ModuleType("tool_policy")
    fake.call_text = lambda i: str(i)
    fake.evaluate_hook = lambda payload, *a, **k: {
        "hookSpecificOutput": {"hookEventName": "PreToolUse",
                               "permissionDecision": "deny",
                               "permissionDecisionReason": "payment: never for tris"}}
    monkeypatch.setitem(sys.modules, "tool_policy", fake)
    out = hp.pre_tool_call("terminal", {"command": "stripe charges create"})
    assert out == {"action": "block", "message": "payment: never for tris"}


def test_whoami_reports_whether_the_guards_are_in_force(as_tris):
    import json
    d = json.loads(hp.whoami_handler())
    assert d["in_force"] is True and d["principal"] == "tris" and d["actor"] == "tris"
    assert d["ledger_log"].endswith("events/tris.jsonl")


def test_registration_wires_both_hooks_and_the_tool(as_tris):
    calls = {"hooks": [], "tools": []}

    class Ctx:
        def register_hook(self, name, cb): calls["hooks"].append((name, cb))
        def register_tool(self, **kw): calls["tools"].append(kw["name"])

    hp.register(Ctx())
    assert [n for n, _ in calls["hooks"]] == ["pre_tool_call", "on_session_start"]
    assert calls["tools"] == ["datacore_whoami"]


def test_registration_survives_a_runtime_that_rejects_the_tool(as_tris):
    class Ctx:
        def __init__(self): self.hooks = []
        def register_hook(self, name, cb): self.hooks.append(name)
        def register_tool(self, **kw): raise RuntimeError("toolset unknown")

    c = Ctx()
    hp.register(c)                      # must not raise: the hooks are the point
    assert c.hooks == ["pre_tool_call", "on_session_start"]
