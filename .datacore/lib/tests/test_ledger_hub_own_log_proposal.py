"""A refused event in the hub's OWN log is a rejected proposal, not the end of it.

On 2026-10-28 Winston, the grantor of 2-datacore, wrote two events of a retired
type (`task.create`) into his own log with a stale copy of the ledger library.
A writer's refused event is kept as a rejected proposal once the grantor
records a `ledger.reject` for it; the grantor's own log is admitted before any
reject is read, so nothing could ever rescue it: every later claim, release and
seal of his counted for nothing. `ledger_cli void` refuses a control record and
the write gate refuses any edit, so the log could not be repaired either.

The grantor is the authority that writes those rejects, so an event in its own
log that admission refuses for what it is (`PROPOSAL_CODES`) stays in the chain
with no effect, and the chain goes on. A writer's log keeps the existing rule.
"""
from __future__ import annotations

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from ledger import hub as H
from ledger.log import read_events


def _admission(space):
    events = list(read_events(space))
    auth = H.authority(space, H.require_config_or_no_hub(space, events, None), None)
    return H.admit(events, auth, now_ms=H._wall_ms()), events


def test_a_refused_type_in_the_grantors_own_log_does_not_end_it(env, monkeypatch):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)   # the test's signing keys
    h = env.hub(eligibility=None)
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "before", "assignee": "own"})
    bad = T._signed_into(env, h, "task.create", {"id": "x", "title": "written by a stale library"})
    after = T._signed_into(env, h, "item.create", {"id": "after", "assignee": "own"},
                           hlc=f"{int(bad.hlc.split('.')[0]) + 1:013d}.0000.{h.actor}")

    res, events = _admission(h.space_dir)
    assert h.actor not in res.refused, res.refused
    admitted = {e.hash for e in res.events}
    assert bad.hash in admitted and after.hash in admitted
    state = H.authoritative_state(h.space_dir)
    assert "after" in state.items                     # the grantor's later work counts
    assert "x" not in state.items                     # the refused event has no effect


def test_a_writers_refused_type_still_ends_its_log_without_a_reject(env, monkeypatch):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub(eligibility=None)
    wa = env.writer("wa", H.LocalTransport(h))
    wa.append("item.create", {"id": "t", "assignee": "wa"})
    T._signed_into(env, wa, "task.create", {"id": "x"})
    res, _ = _admission(wa.space_dir)
    assert "wa" in res.refused and "unknown_type" in res.refused["wa"], res.refused
