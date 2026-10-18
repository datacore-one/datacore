"""The first role pool on the hub (DIP-0034, October 2026 amendment, section F).

Written before the code (task 7727bbff). One block per DONE_WHEN line:

  1. a pool item carrying role=researcher is leased only to an actor whose live
     membership in that hub holds the role (`member.role`: actor, role, from,
     until); an actor without it, or whose membership expired, is refused -- in
     the hub AND in the fold;
  2. the daily budget (`pool.budget` for a role in a space, spending from the
     existing `spend.record` events) stops NEW leases once reached; existing
     leases are unaffected; the next day resets;
  3. a pool item's completion needs a DONE_WHEN, and is accepted only after a
     check (`item.verify`) by an actor other than the lease holder; the
     holder's own check is refused;
  4. the assignee rule stays the default: neither assignee nor role is still
     not dispatched; addressed items behave exactly as before;
  5. everything is off unless the space opts in to pools.

Plus a worked example: one research space with the researcher role.

Keys are generated in tmp dirs; the real ~/.datacore/keys is never touched.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from ledger import hub as hubmod
from ledger.events import Event, body_dict, compute_hash
from ledger.fold import fold, pool_accepted, role_live
from ledger.log import read_events
from test_ledger_hub import GRANTOR, HUB_ID, Clock, allow_any, git_pull

DAY_MS = 24 * 3600 * 1000
#: 2027-01-15T08:00:00Z -- far enough from midnight that a few hours do not cross it.
START = 1_800_000_000_000
DONE = "a sourced summary of three competitors is attached and a reviewer has read it"


class PoolEnv:
    def __init__(self, tmp: Path, monkeypatch, *, pools: bool = True):
        import ledger.hlc as hlc
        import ledger.keys as keys
        self.tmp = tmp
        self.keys_dir = tmp / "keys"
        self.registry = tmp / "registry.yaml"
        monkeypatch.setattr(keys, "DATACORE_ROOT", tmp / "root")
        monkeypatch.setenv("DATACORE_ACTOR", "pooltest-host")
        monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)
        monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
        self.clock = Clock(START)
        # Every HLC stamp follows the test clock, so "the day" of a spend is the
        # hub's day and a simulated midnight is a real one.
        monkeypatch.setattr(hlc, "time", types.SimpleNamespace(time=lambda: self.clock() / 1000))
        self.hub_dir = tmp / "hub-space"
        (self.hub_dir / ".datacore" / "events").mkdir(parents=True)
        hubmod.enable(self.hub_dir, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900)
        if pools:
            hubmod.enable_pools(self.hub_dir)
        self.hubs: list = []
        self._hub = None
        self.t = None

    def hub(self, *, eligibility=None, state: str = "state") -> "hubmod.Hub":
        h = hubmod.Hub(self.hub_dir, GRANTOR, keys_dir=self.keys_dir, registry_path=self.registry,
                       state_dir=self.tmp / state / GRANTOR, clock=self.clock, eligibility=eligibility)
        self.hubs.append(h)
        return h

    def start(self, **kw):
        self._hub = self.hub(**kw)
        self.t = hubmod.LocalTransport(self._hub)
        return self._hub

    def writer(self, actor: str) -> "hubmod.HubWriter":
        space = self.tmp / f"{actor}-space"
        if not space.exists():
            (space / ".datacore" / "events").mkdir(parents=True)
            git_pull(self.hub_dir, space)
        return hubmod.HubWriter(space, actor, self.t, keys_dir=self.keys_dir, registry_path=self.registry,
                                state_dir=self.tmp / "wstate" / actor, clock=self.clock)

    def item(self, iid: str):
        return fold(read_events(self.hub_dir)).items[iid]

    def close(self):
        for h in self.hubs:
            h.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = PoolEnv(tmp_path, monkeypatch)
    yield e
    e.close()


def pool_item(owner, iid: str, *, role: str = "researcher", done_when: str | None = DONE, **extra):
    payload = {"id": iid, "title": f"pool item {iid}", "role": role, **extra}
    if done_when is not None:
        payload["done_when"] = done_when
    owner.append("item.create", payload)


def refused(fn, *args) -> "hubmod.HubRefused":
    with pytest.raises(hubmod.HubRefused) as exc:
        fn(*args)
    return exc.value


# ── 1. role membership decides who may lease ────────────────────────────────

def test_pool_item_is_leased_only_to_a_live_member_with_the_role(env):
    hub = env.start()
    own, member, outsider, other_role = env.writer("own"), env.writer("wa"), env.writer("wb"), env.writer("wc")
    hub.record_role("wa", "researcher")
    hub.record_role("wc", "scout")
    pool_item(own, "p1")

    assert refused(outsider.request_lease, "p1").code == "not_eligible"     # no membership
    assert refused(other_role.request_lease, "p1").code == "not_eligible"   # a different role
    assert member.request_lease("p1")["status"] == "granted"
    item = env.item("p1")
    assert item.lease_holder == "wa" and item.status == "claimed"
    assert [e.payload["actor"] for e in read_events(env.hub_dir) if e.type == "member.role"] == ["wa", "wc"]


def test_a_membership_that_has_expired_or_not_started_is_refused(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    now = env.clock()
    hub.record_role("wa", "researcher", until_ms=now + 60_000)
    hub.record_role("wb", "researcher", from_ms=now + 3_600_000)
    pool_item(own, "p1")
    pool_item(own, "p2")

    assert refused(wb.request_lease, "p1").code == "not_eligible"            # not started yet
    env.clock.advance(120)
    assert refused(wa.request_lease, "p1").code == "not_eligible"            # expired
    env.clock.advance(3600)
    assert wb.request_lease("p1")["status"] == "granted"                     # now live


def test_the_fold_refuses_a_lease_to_an_actor_without_the_role(env):
    """A hub that grants anyway (old code, a bug, a forged grantor) does not
    make the lease real: every copy folds it to a recorded conflict."""
    lax = env.start(eligibility=allow_any)
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    lax.record_role("wa", "researcher", until_ms=env.clock() + 60_000)
    pool_item(own, "p1")
    pool_item(own, "p2")

    assert wb.request_lease("p1")["status"] == "granted"                     # the hub said yes ...
    item = env.item("p1")
    assert item.lease_holder is None and item.status == "created", item.history  # ... the fold says no
    assert any("role" in why for why in item.lease_conflicts.values())

    env.clock.advance(120)                                                   # wa's membership ends
    assert wa.request_lease("p2")["status"] == "granted"
    item = env.item("p2")
    assert item.lease_holder is None and any("role" in w for w in item.lease_conflicts.values())

    copy = env.tmp / "copy"
    (copy / ".datacore" / "events").mkdir(parents=True)
    git_pull(env.hub_dir, copy)
    assert fold(read_events(copy)).state_root() == fold(read_events(env.hub_dir)).state_root()


def test_membership_is_recorded_only_by_the_hub_and_only_for_known_roles(env):
    hub = env.start()
    wa = env.writer("wa")
    # A writer cannot grant itself a role: member.role and pool.budget are hub-only.
    for etype, payload in (("member.role", {"actor": "wa", "role": "researcher"}),
                           ("pool.budget", {"role": "researcher", "cents_per_day": 10**9})):
        e = wa.log.append(etype, payload)
        import json
        from ledger.events import to_line
        assert refused(hub.append, {"event": json.loads(to_line(e))}).code == "hub_only_type"
    # Roles are the ones defined in .datacore/templates/roles/.
    assert refused(hub.record_role, "wa", "astronaut").code == "unknown_role"
    st = fold(read_events(env.hub_dir))
    assert not role_live(st, HUB_ID, GRANTOR, "wa", "researcher", env.clock())


# ── 2. the daily budget ─────────────────────────────────────────────────────

def test_daily_budget_stops_new_leases_keeps_existing_ones_and_resets_next_day(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    for w in ("wa", "wb"):
        hub.record_role(w, "researcher")
    hub.set_budget("researcher", 500)
    for i in range(4):
        pool_item(own, f"p{i}")

    assert wa.request_lease("p0")["status"] == "granted"
    wa.append("spend.record", {"item": "p0", "cents": 300, "model": "test"})
    assert wb.request_lease("p1")["status"] == "granted"                    # 300 < 500: still open
    wb.append("spend.record", {"item": "p1", "cents": 250, "model": "test"})

    # 550 >= 500: the cap is reached. No NEW lease ...
    assert refused(wa.request_lease, "p2").code == "budget_reached"
    # ... but the existing leases carry on: renew, spend, complete.
    env.clock.advance(300)
    assert wb.renew_lease("p1")["status"] == "renewed"
    wb.append("spend.record", {"item": "p1", "cents": 50, "model": "test"})
    wb.append("item.complete", {"id": "p1"})
    assert env.item("p1").status == "completed"
    assert env.item("p0").lease_holder == "wa"

    # Spending on unpooled work does not count against the pool.
    own.append("item.create", {"id": "solo", "title": "addressed", "assignee": "wa"})
    # The next UTC day resets the count.
    env.clock.advance(DAY_MS / 1000)
    assert wa.request_lease("p2")["status"] == "granted"


def test_a_role_without_a_budget_is_not_capped_and_a_budget_is_per_role(env):
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    hub.record_role("wa", "researcher")
    hub.record_role("wa", "scout")
    hub.set_budget("scout", 100)
    pool_item(own, "r1")
    pool_item(own, "s1", role="scout")
    pool_item(own, "s2", role="scout")
    pool_item(own, "r2")
    assert wa.request_lease("s1")["status"] == "granted"
    wa.append("spend.record", {"item": "s1", "cents": 100, "model": "test"})
    assert refused(wa.request_lease, "s2").code == "budget_reached"
    assert wa.request_lease("r1")["status"] == "granted"                     # researcher: no cap
    wa.append("spend.record", {"item": "r1", "cents": 10_000, "model": "test"})
    assert wa.request_lease("r2")["status"] == "granted"


# ── 3. DONE_WHEN and an independent check ───────────────────────────────────

def test_a_pool_item_without_done_when_is_not_leased(env):
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    hub.record_role("wa", "researcher")
    pool_item(own, "vague", done_when=None)
    pool_item(own, "org", done_when=None, org={"properties": {"DONE_WHEN": DONE}})
    err = refused(wa.request_lease, "vague")
    assert err.code == "not_eligible" and "DONE_WHEN" in err.message
    assert wa.request_lease("org")["status"] == "granted"                    # the org property counts


def test_completion_is_accepted_only_after_another_actors_check(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    hub.record_role("wa", "researcher")
    pool_item(own, "p1")
    wa.request_lease("p1")
    wa.append("item.complete", {"id": "p1", "result": "summary attached"})
    item = env.item("p1")
    assert item.status == "completed" and not pool_accepted(item)            # done, not yet accepted

    # The holder's own check is refused: the writer refuses before writing ...
    before = (wa.space_dir / ".datacore" / "events" / "wa.jsonl").read_bytes()
    assert refused(wa.append, "item.verify", {"id": "p1"}).code == "own_check"
    assert (wa.space_dir / ".datacore" / "events" / "wa.jsonl").read_bytes() == before
    # ... and a raw holder check that reaches the hub anyway is not counted.
    raw = wa.log.append("item.verify", {"id": "p1"})
    import json
    from ledger.events import to_line
    receipt = hub.append({"event": json.loads(to_line(raw)), "log": "wa"})
    assert receipt["status"] == "accepted" and "own_check" in receipt.get("not_counted", "")
    item = env.item("p1")
    assert item.status == "completed" and not pool_accepted(item), item.history

    # A different actor's check is what accepts it.
    wb.append("item.verify", {"id": "p1", "check": DONE})
    item = env.item("p1")
    assert item.status == "verified" and pool_accepted(item)


def test_the_fold_refuses_a_pool_completion_when_done_when_was_removed(env):
    """DONE_WHEN is needed at completion too, not only at the lease."""
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    hub.record_role("wa", "researcher")
    pool_item(own, "p1")
    wa.request_lease("p1")
    own.append("item.update", {"id": "p1", "done_when": ""})
    git_pull(env.hub_dir, wa.space_dir)
    assert refused(wa.append, "item.complete", {"id": "p1"}).code == "no_done_when"
    raw = wa.log.append("item.complete", {"id": "p1"})
    import json
    from ledger.events import to_line
    receipt = hub.append({"event": json.loads(to_line(raw)), "log": "wa"})
    assert "no_done_when" in receipt.get("not_counted", "")
    item = env.item("p1")
    assert item.status == "claimed" and any("DONE_WHEN" in h for h in item.history)


# ── 4. the assignee rule is still the default ───────────────────────────────

def test_with_pools_on_unaddressed_items_wait_and_addressed_items_are_unchanged(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    hub.record_role("wa", "researcher")
    hub.set_budget("researcher", 0)                                          # a closed pool
    own.append("item.create", {"id": "nobody", "title": "unaddressed"})
    own.append("item.create", {"id": "mine", "title": "addressed", "assignee": "wa"})
    own.append("item.create", {"id": "both", "title": "both", "assignee": "wa", "role": "researcher",
                               "done_when": DONE})
    assert refused(wa.request_lease, "nobody").code == "not_eligible"
    assert refused(wa.request_lease, "both").code == "not_eligible"
    assert refused(wb.request_lease, "mine").code == "not_eligible"
    # Addressed work needs no role, no DONE_WHEN and ignores the pool's budget.
    assert wa.request_lease("mine")["status"] == "granted"
    wa.append("item.complete", {"id": "mine"})
    assert env.item("mine").status == "completed"


# ── 5. off by default ───────────────────────────────────────────────────────

def test_pools_are_off_unless_the_space_opts_in(tmp_path, monkeypatch):
    e = PoolEnv(tmp_path, monkeypatch, pools=False)
    try:
        assert hubmod.load_config(e.hub_dir).pools is False
        hub = e.start()
        own, wa = e.writer("own"), e.writer("wa")
        assert refused(hub.record_role, "wa", "researcher").code == "pools_off"
        assert refused(hub.set_budget, "researcher", 100).code == "pools_off"
        pool_item(own, "p1")
        err = refused(wa.request_lease, "p1")
        assert err.code == "not_eligible" and "not enabled" in err.message
        # A space with no hub at all has no pools either.
        plain = tmp_path / "plain"
        (plain / ".datacore").mkdir(parents=True)
        assert hubmod.load_config(plain) is None
        with pytest.raises(hubmod.HubRefused) as exc:
            hubmod.enable_pools(plain)
        assert exc.value.code == "hub_off"
    finally:
        e.close()


def test_folding_a_log_without_pool_events_hashes_as_before():
    """Old logs never contained member.role / pool.budget / hub spend: the new
    state fields must not enter their state root."""
    def ev(seq, etype, payload, actor="own"):
        body = body_dict(seq, f"{1_700_000_000_000 + seq:013d}.0000.{actor}", actor, etype, payload, "x")
        return Event(**body, hash=compute_hash(body), sig="")
    events = [ev(0, "item.create", {"id": "t", "title": "x", "role": "researcher"}),
              ev(1, "spend.record", {"item": "t", "cents": 40})]
    st = fold(events)
    assert st.members == {} and st.budgets == {} and st.pool_spend == {}
    from dataclasses import fields
    from ledger.fold import LedgerState
    legacy = LedgerState(items=st.items, spend=st.spend, orphans=st.orphans)
    assert st.state_root() == legacy.state_root()
    assert {f.name for f in fields(LedgerState)} >= {"items", "spend", "orphans"}


# ── worked example: a research space with the researcher role ───────────────

def test_worked_example_research_space(env):
    """The whole loop for one space, as the owner would run it.

    Owner, once:   python3 -m ledger.hub enable <space> --hub-id research-hub --grantor hubd
                   python3 -m ledger.hub pools <space> --on
    Hub, as facts: record_role("wa", "researcher"); set_budget("researcher", 2_000)
    Owner adds:    an item with role=researcher and a DONE_WHEN
    Agent wa:      leases it, spends, completes
    Agent wb:      checks it against the DONE_WHEN -> accepted
    """
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    hub.record_role("wa", "researcher", until_ms=env.clock() + 30 * DAY_MS)
    hub.set_budget("researcher", 2_000)
    own.append("item.create", {"id": "competitor-scan", "title": "Scan three competitors' pricing pages",
                               "role": "researcher", "done_when": DONE})
    assert refused(wb.request_lease, "competitor-scan").code == "not_eligible"
    assert wa.request_lease("competitor-scan")["status"] == "granted"
    wa.append("spend.record", {"item": "competitor-scan", "cents": 120, "model": "test"})
    wa.append("item.complete", {"id": "competitor-scan", "result": "summary attached"})
    wb.append("item.verify", {"id": "competitor-scan", "check": DONE})
    state = fold(read_events(env.hub_dir))
    assert pool_accepted(state.items["competitor-scan"])
    assert state.pool_spend == {"researcher|2027-01-15": 120}
