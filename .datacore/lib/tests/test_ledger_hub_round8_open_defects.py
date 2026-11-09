"""The four defects the round-8 GLM re-check left open (verdict
2026-10-23-ledger-hub-round8-glm-recheck-verdict.md, rows C1-C4), as
regression tests that assert the CORRECT behaviour (plan phase D, part 1).

C1  an owner-attested hand-over from a copy that lacks the old hub's last seal
    must be refused unless the owner names that seal and the copy holds it;
C2  an earlier-stamped rival claim that arrives by git after a seal never shows
    as the owner in the live view, not even before the next seal;
C3  the owner's pool budget still binds after a hand-over;
C4  an exact retry of a lease request whose answer was lost gets the stored
    answer even when the writer's key was rotated in between.
"""
from __future__ import annotations

import time

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_role_pool import PoolEnv, pool_item
from ledger import hub as H
from ledger.events import to_line


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    e = T.Env(tmp_path, monkeypatch)
    yield e
    e.close()


@pytest.fixture
def pool_env(tmp_path, monkeypatch):
    e = PoolEnv(tmp_path, monkeypatch)
    yield e
    e.close()


# ── C1: the guard cannot know the old hub's true last seal, so it asks ──────

def _sealed_claim_then_truncated_copy(env):
    """Hub seals twice (the second seal covers wa's claim), stops, and the
    owner's copy of the grantor log has lost the last seal. Returns
    (writers, original claim, grantor log path, full log bytes, last seal hash)."""
    h = env.hub()
    own, wa, wb = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa", "wb")]
    ms = int(time.time() * 1000)
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{ms:013d}.0000.own")))
    h.seal()
    gpath = env.hub_dir / ".datacore/events" / f"{T.GRANTOR}.jsonl"
    keep = len(gpath.read_bytes().splitlines())
    original = T._signed_into(env, wa, "item.claim", {"id": "t"}, hlc=f"{ms + 200:013d}.0000.wa")
    h.append(wa._submission(original))
    last_seal = h.seal()["seal"]["hash"]
    assert wa.receipt_check(env.hub_dir, required=[original.hash])["status"] == "confirmed"
    h.close()
    full = gpath.read_bytes()
    gpath.write_bytes(b"".join(full.splitlines(keepends=True)[:keep]))
    return (own, wa, wb), original, gpath, full, last_seal, ms


def _promote(env, **kw):
    return H.promote(env.hub_dir, "hubb", by=T.OWNER, reason="recover a stopped hub", attest_stopped=True,
                     keys_dir=env.keys_dir, registry_path=env.registry, clock=env.clock, **kw)


def test_c1_attested_promotion_without_the_named_last_seal_is_refused(env):
    _sealed_claim_then_truncated_copy(env)
    with pytest.raises(H.HubRefused) as exc:
        _promote(env)
    assert exc.value.code == "last_seal_unknown"
    assert "last_seal" in str(exc.value) or "--last-seal" in str(exc.value)


def test_c1_attested_promotion_naming_a_seal_the_copy_lacks_is_refused(env):
    _, _, _, _, last_seal, _ = _sealed_claim_then_truncated_copy(env)
    with pytest.raises(H.HubRefused) as exc:
        _promote(env, last_seal=last_seal)
    assert exc.value.code == "behind_predecessor"


def test_c1_attested_promotion_naming_an_older_seal_than_the_copy_holds_is_refused(env):
    """The owner's statement is contradicted by the copy: refuse, never lower the floor."""
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    first = h.seal()["seal"]["hash"]
    own.append("item.update", {"id": "t", "title": "x"})
    h.seal()
    h.close()
    with pytest.raises(H.HubRefused) as exc:
        _promote(env, last_seal=first)
    assert exc.value.code == "last_seal_unknown"


def test_c1_attested_promotion_with_the_named_last_seal_held_keeps_the_confirmed_claim(env):
    (own, wa, wb), original, gpath, full, last_seal, ms = _sealed_claim_then_truncated_copy(env)
    gpath.write_bytes(full)                               # the owner restores the full log
    _promote(env, last_seal=last_seal)
    promo = [e for e in __import__("ledger.log", fromlist=["read_events"]).read_events(env.hub_dir)
             if e.type == "hub.promote"][-1]
    assert promo.payload["predecessor"]["last_seal"]["hash"] == last_seal
    env.clock.advance(10 ** 7)
    succ = env.hub("hubb")
    late = T._signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{ms + 100:013d}.0000.wb")
    try:
        succ.append(wb._submission(late))
    except H.HubRefused:
        pass
    succ.seal()
    assert wa.receipt_check(env.hub_dir, required=[original.hash])["status"] == "confirmed"
    assert succ._state()[1].items["t"].owner == "wa"


# ── C2: the live view never shows a rival that reorders a sealed decision ───

def test_c2_earlier_rival_arriving_by_git_after_a_seal_never_shows_in_the_live_view(env):
    h = env.hub()
    own, wa, wb = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa", "wb")]
    ms = int(time.time() * 1000)
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{ms:013d}.0000.own")))
    original = T._signed_into(env, wa, "item.claim", {"id": "t"}, hlc=f"{ms + 200:013d}.0000.wa")
    h.append(wa._submission(original))
    h.seal()
    late = T._signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{ms + 100:013d}.0000.wb")
    with (env.hub_dir / ".datacore/events/wb.jsonl").open("a") as f:     # by git, never at the door
        f.write(to_line(late) + "\n")
    assert H.authoritative_state(env.hub_dir, registry_path=env.registry).items["t"].owner == "wa"
    assert h._state()[1].items["t"].owner == "wa"
    assert H.authoritative_state(env.hub_dir, registry_path=env.registry, settled=True).items["t"].owner == "wa"
    h.seal()
    assert H.authoritative_state(env.hub_dir, registry_path=env.registry).items["t"].owner == "wa"
    assert wa.receipt_check(env.hub_dir, required=[original.hash])["status"] == "confirmed"


# ── C3: the pool budget binds across a hand-over ────────────────────────────

def test_c3_pool_budget_still_binds_after_a_handover(pool_env):
    env = pool_env
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    hub.set_budget("researcher", 500)
    for i in range(3):
        pool_item(own, f"p{i}")
    assert wa.request_lease("p0")["status"] == "granted"
    wa.append("spend.record", {"item": "p0", "cents": 600, "model": "test"})
    with pytest.raises(H.HubRefused) as exc:
        wb.request_lease("p1")
    assert exc.value.code == "budget_reached"
    hub.seal()
    hub.fence("handover")
    H.promote(env.hub_dir, "hubb", by=T.OWNER, reason="handover", keys_dir=env.keys_dir,
              registry_path=env.registry, clock=env.clock)
    env.clock.advance(2000)
    succ = H.Hub(env.hub_dir, "hubb", keys_dir=env.keys_dir, registry_path=env.registry,
                 state_dir=env.tmp / "state" / "hubb", clock=env.clock, policy=env.policy)
    env.hubs.append(succ)
    env.t.hub = succ
    T.git_pull(env.hub_dir, wb.space_dir)
    wb = env.writer("wb")
    with pytest.raises(H.HubRefused) as exc:
        wb.request_lease("p1")
    assert exc.value.code == "budget_reached"


# ── C4: a lost answer comes back however late, even after a key rotation ────

def test_c4_lost_lease_answer_retried_after_key_rotation_gets_the_stored_grant(env):

    class Lossy(H.LocalTransport):
        n = 0

        def request_lease(self, req):
            answer = super().request_lease(req)
            Lossy.n += 1
            if Lossy.n == 1:
                self.lost = answer
                raise H.HubUnavailable("answer lost")
            return answer

    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    transport = Lossy(h)
    wa = env.writer("wa", transport)
    with pytest.raises(H.HubUnavailable):
        wa.request_lease("t")
    env.clock.advance(10)
    T._rotate_in_principals(env, "wa", env.clock() - 1)
    answer = wa.request_lease("t")
    assert answer.get("status") == "granted"
    assert answer == transport.lost
