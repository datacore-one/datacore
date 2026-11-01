"""The nightly invariants compare the hub's admitted log with the git copy
(plan 2026-10-31, phase H; processes page, "Ledger hygiene", what changes [1]).

Once the hub is the delivery path, the git copy must be checked against it:
an event the hub admitted that never reached git, or an event in git the hub
never admitted, past a grace window, is a finding (`delivered`). A hub that
does not answer, or is not named, is could-not-tell -- never a pass. Reuses
`ledger.hub.task_events_hub_first` / `transport_from_env` (phase D2).
Written before the code.
"""
from __future__ import annotations

import time

import pytest

from tests import test_ledger_hub as T
from ledger import hub as H

HOUR_MS = 3600 * 1000


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    monkeypatch.delenv("DATACORE_HUB_URL", raising=False)
    monkeypatch.delenv("DATACORE_HUB_TOKEN_ID", raising=False)
    e = T.Env(tmp_path, monkeypatch)
    e.clock.now = int(time.time() * 1000)
    yield e
    e.close()


class Down:
    def events(self, since=None):
        raise H.HubUnavailable("down")

    def status(self):
        raise H.HubUnavailable("down")

    def __getattr__(self, name):
        def _down(*_a, **_k):
            raise H.HubUnavailable("down")
        return _down


def _copy(env, name):
    space = env.tmp / name
    (space / ".datacore" / "events").mkdir(parents=True)
    T.git_pull(env.hub_dir, space)
    return space


def _delivered(space, transport, now_ms, env):
    import ledger_invariants as inv
    return inv.delivered(space, transport, now_ms=now_ms, registry_path=env.registry)


def test_a_copy_in_step_with_the_hub_has_no_finding(env):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t1", "title": "one"})
    copy = _copy(env, "in-step")
    found = _delivered(copy, H.LocalTransport(h), int(time.time() * 1000) + 3 * HOUR_MS, env)
    assert found == [], [str(f) for f in found]


def test_an_event_the_hub_admitted_that_never_reached_git_is_flagged_after_the_grace(env):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t1", "title": "one"})
    copy = _copy(env, "stale")                       # taken now, never pulled again
    own.append("item.create", {"id": "t2", "title": "admitted, not in git"})
    soon = _delivered(copy, H.LocalTransport(h), int(time.time() * 1000) + 60 * 1000, env)
    assert soon == [], "inside the grace window it is ordinary lag"
    late = _delivered(copy, H.LocalTransport(h), int(time.time() * 1000) + 3 * HOUR_MS, env)
    assert [f.invariant for f in late if not f.unknown] == ["delivered"], [str(f) for f in late]
    assert "never reached git" in late[0].detail and "own" in late[0].detail


def test_an_event_in_git_the_hub_never_admitted_is_flagged(env):
    h = env.hub()
    online = env.writer("own", H.LocalTransport(h))
    online.append("item.create", {"id": "t1", "title": "one"})
    offline = env.writer("wa", Down())                # writes locally, the hub never hears of it
    offline.append("item.create", {"id": "t2", "title": "only in git"})
    found = _delivered(offline.space_dir, H.LocalTransport(h), int(time.time() * 1000) + 3 * HOUR_MS, env)
    bad = [f for f in found if not f.unknown]
    assert [f.invariant for f in bad] == ["delivered"], [str(f) for f in found]
    assert "never admitted" in bad[0].detail and "wa" in bad[0].detail


def test_a_hub_that_does_not_answer_is_could_not_tell_never_a_pass(env):
    env.hub()
    copy = _copy(env, "c")
    found = _delivered(copy, Down(), int(time.time() * 1000), env)
    assert found and all(f.unknown for f in found) and found[0].invariant == "delivered", found


def test_a_hub_space_with_no_hub_named_is_could_not_tell(env):
    env.hub()
    copy = _copy(env, "c")
    found = _delivered(copy, None, int(time.time() * 1000), env)
    assert found and all(f.unknown for f in found), found


def test_a_space_without_a_hub_is_not_compared(tmp_path):
    import ledger_invariants as inv
    space = tmp_path / "1-plain"
    (space / ".datacore" / "events").mkdir(parents=True)
    assert inv.delivered(space, None) == []


def test_the_sweep_runs_the_comparison_on_every_hub_space(env, monkeypatch):
    import ledger_invariants as inv
    env.hub()
    root = env.tmp / "root"
    root.mkdir()
    space = root / "1-hubbed"
    (space / ".datacore" / "events").mkdir(parents=True)
    T.git_pull(env.hub_dir, space)
    seen = []
    monkeypatch.setattr(inv, "delivered", lambda sp, transport, **kw: seen.append(sp.name) or [])
    monkeypatch.setattr(H, "transport_from_env", lambda sp, consumer, **kw: None)
    inv.sweep(root)
    assert seen == ["1-hubbed"], seen
    seen.clear()
    inv.sweep(root, quick=True)
    assert seen == [], "--quick skips the network comparison, like the publish check"
