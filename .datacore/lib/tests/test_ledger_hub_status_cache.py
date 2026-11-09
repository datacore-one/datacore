"""The hub's status report does not rebuild the whole space on every call (2026-10-24).

WHY. In 1-datafund (180,000 events) each GET /ledger/hub/<space>/status took
about 19 s: status admitted and folded the whole space every time, so a
writer's 10 s HTTP timeout could never see it. The report now reuses the last
build while the space's log files are unchanged (at most 60 s); anything
written invalidates it, and the time-dependent fields are computed per call.
"""
from __future__ import annotations

from ledger import hub as hubmod

from tests.test_ledger_hub import env  # noqa: F401  (fixture)


def test_status_reuses_the_build_until_a_log_changes(env, monkeypatch):
    hub = env.hub(eligibility=None)
    writer = env.writer("own", hubmod.LocalTransport(hub))
    writer.append("item.create", {"id": "t1", "title": "one"})
    calls = []
    real = hubmod.Hub._state
    monkeypatch.setattr(hubmod.Hub, "_state", lambda self: (calls.append(1), real(self))[1])
    first = hub.status()
    second = hub.status()
    assert len(calls) == 1, "an unchanged space was rebuilt"
    assert first["watermarks"] == second["watermarks"]
    writer.append("item.create", {"id": "t2", "title": "two"})
    calls.clear()                      # the append itself reads the state
    third = hub.status()
    assert len(calls) == 1 and third["watermarks"] != first["watermarks"]


def test_the_cached_report_expires(env, monkeypatch):
    hub = env.hub(eligibility=None)
    calls = []
    real = hubmod.Hub._state
    monkeypatch.setattr(hubmod.Hub, "_state", lambda self: (calls.append(1), real(self))[1])
    hub.status()
    env.clock.advance(61)
    hub.status()
    assert len(calls) == 2
