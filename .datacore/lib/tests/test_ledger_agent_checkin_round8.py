"""Round 8 (check-in): the round-8 audit's F8-4, assertion turned around.

The hub's own presence view reads check-ins through the one correction rule,
exactly like the standalone reader: a check-in the hub kept as a rejected
proposal is not a check-in. Seen failing on bcb1d02b."""
from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from ledger import hub as H
from ledger.presence import admitted_events, presence


def test_r8_f8_4_hub_presence_ignores_a_rejected_checkin(env):
    h = env.hub()
    own, wa = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa")]
    ms = T._real_ms()
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{ms:013d}.0000.own")))
    h.seal()
    checkin = T._signed_into(env, wa, "agent.checkin", {"id": "t", "agent": "wa", "interval_s": 900, "ts": ms},
                             hlc=f"{ms:013d}.0000.own")
    assert h.append(wa._submission(checkin, chain="wa"))["status"] == "rejected"
    direct = h.presence(agents=["wa"])["agents"]["wa"]
    copied = presence(admitted_events(env.hub_dir, env.clock(), registry_path=env.registry),
                      env.clock(), agents=["wa"], registry_path=env.registry)["wa"]
    assert direct["last_ts"] is None, "the hub's presence view counts a check-in it rejected"
    assert direct == copied
