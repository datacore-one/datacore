"""Agent check-ins under the round-6 correction rule (Codex round 6, the
check-in variant of N4): a check-in the owner reversed is no evidence that
the agent is alive. Written from the auditor's probe, assertion turned around."""
from tests.test_ledger_agent_checkin import OWNER, env  # noqa: F401
from ledger import hub as H
from ledger import presence


def test_r6_a_reversed_checkin_is_not_counted(env):
    env.checkin("miles")
    events, _ = env.hub._state()
    e = next(x for x in events if x.type == "agent.checkin")
    owner = H.HubWriter(env.space_of(OWNER), OWNER, env.transport, keys_dir=env.keys_dir,
                        registry_path=env.registry, state_dir=env.tmp / "owner-state", clock=env.clock)
    v, _ = owner.append("ledger.void", {"log": e.log, "seq": e.seq, "hash": e.hash, "reason": "wrong health evidence"})
    env.hub.seal()
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"
    got = presence.presence(presence.admitted_events(env.hub_dir, env.clock(), registry_path=env.registry),
                            env.clock(), agents=["miles"], registry_path=env.registry)
    assert got["miles"]["state"] != "ok", got
