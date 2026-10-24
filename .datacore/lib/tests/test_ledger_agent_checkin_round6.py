"""Agent check-ins under the correction rule (Codex round 6, the check-in
variant of N4), updated for the owner's round-7 decision: a check-in is a
control record (it gates the dispatcher's view of who is alive) and is never
reversible. A reversal naming one is refused; the reader's guarantee -- a
reversed record is no evidence -- is kept, checked with the allow-list
widened for one test."""
import pytest

from tests.test_ledger_agent_checkin import OWNER, env  # noqa: F401
from ledger import finality
from ledger import hub as H
from ledger import presence


def _owner(env):
    return H.HubWriter(env.space_of(OWNER), OWNER, env.transport, keys_dir=env.keys_dir,
                       registry_path=env.registry, state_dir=env.tmp / "owner-state", clock=env.clock)


def _checkin_event(env):
    env.checkin("miles")
    events, _ = env.hub._state()
    return next(x for x in events if x.type == "agent.checkin")


def test_r7_a_checkin_is_never_reversible(env):
    e = _checkin_event(env)
    with pytest.raises(H.HubRefused) as exc:
        _owner(env).append("ledger.void", {"log": e.log, "seq": e.seq, "hash": e.hash,
                                           "reason": "wrong health evidence"})
    assert exc.value.code == "not_reversible"
    env.hub.seal()
    got = presence.presence(presence.admitted_events(env.hub_dir, env.clock(), registry_path=env.registry),
                            env.clock(), agents=["miles"], registry_path=env.registry)
    assert got["miles"]["state"] == "ok", got


def test_r6_a_reversed_checkin_is_not_counted(env, monkeypatch):
    monkeypatch.setattr(finality, "REVERSIBLE_TYPES", finality.REVERSIBLE_TYPES | {"agent.checkin"})
    e = _checkin_event(env)
    owner = _owner(env)
    v, _ = owner.append("ledger.void", {"log": e.log, "seq": e.seq, "hash": e.hash, "reason": "wrong health evidence"})
    env.hub.seal()
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"
    got = presence.presence(presence.admitted_events(env.hub_dir, env.clock(), registry_path=env.registry),
                            env.clock(), agents=["miles"], registry_path=env.registry)
    assert got["miles"]["state"] != "ok", got
