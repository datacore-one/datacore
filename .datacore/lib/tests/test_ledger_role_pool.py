"""Role pools on the hub (DIP-0034, October 2026 amendment, section F), as the
owner decided on 2026-10-18:

  "The role is defined in the task. If the task isn't delegated to any agent
   concretely, an available agent picks it up, loads the role description and
   executes the task. This is not the main scenario; main is that most tasks
   will be directly delegated."

This replaces the first build's membership model (`member.role` grants). The
blocks below are the required behaviour:

  1. a task addressed to a named agent is leased by that agent only (the
     assignee rule, still the default); if it carries a role, the grant names
     the role description to load;
  2. an unaddressed task carrying role=<name> may be leased by ANY available
     agent: a signed request from a writer whose principal is a registered
     agent (`kind: agent` in principals.yaml), while the role's pool is under
     its daily budget. No membership grant is needed. One lease at a time;
  3. the grant names the role description, `.datacore/templates/roles/<role>.md`,
     checked at lease time; a role with no template is refused (`unknown_role`);
  4. an unaddressed task with no role is still not dispatched;
  5. the daily budget still stops new leases; a pool task's completion needs a
     DONE_WHEN and counts only after another actor's check;
  6. everything is off unless the space opts in to pools; old state roots are
     unchanged; `member.role` no longer exists.

Plus a worked example: one research space with the researcher role.

Keys are generated in tmp dirs; the real ~/.datacore/keys, principals.yaml and
role templates are never touched.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from ledger import hub as hubmod
from ledger.events import EVENT_TYPES, Event, body_dict, compute_hash, to_line
from ledger.fold import fold, pool_accepted
from ledger.log import read_events
sys.path.insert(0, str(Path(__file__).parent))
from test_ledger_hub import APPROVER, GRANTOR, HUB_ID, OWNER, TEST_POLICY, Clock, allow_any, git_pull  # noqa: E402

DAY_MS = 24 * 3600 * 1000
#: 2027-01-15T08:00:00Z -- far enough from midnight that a few hours do not cross it.
START = 1_800_000_000_000
DONE = "a sourced summary of three competitors is attached and a reviewer has read it"
RESEARCHER = ".datacore/templates/roles/researcher.md"

#: Agents wa, wb, wc; the owner writes as `own` and is a human; `wz` has a key
#: but no principal.
PRINCIPALS = """\
version: 1
principals:
  owner:
    kind: human
    writes_as: [own]
  wa:
    kind: agent
  wb:
    kind: agent
  wc:
    kind: agent
"""


class PoolEnv:
    def __init__(self, tmp: Path, monkeypatch, *, pools: bool = True):
        import actor_identity
        import ledger.hlc as hlc
        import ledger.keys as keys
        self.tmp = tmp
        self.keys_dir = tmp / "keys"
        self.registry = tmp / "registry.yaml"
        monkeypatch.setattr(keys, "DATACORE_ROOT", tmp / "root")
        monkeypatch.setenv("DATACORE_ACTOR", "pooltest-host")
        monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)
        monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
        # A tmp installation: its principals and its role descriptions.
        install = tmp / "install" / ".datacore"
        (install / "registry").mkdir(parents=True)
        (install / "registry" / "principals.yaml").write_text(PRINCIPALS)
        monkeypatch.setattr(actor_identity, "PRINCIPALS", install / "registry" / "principals.yaml")
        # The policy's explicit inputs, with the writer -> principal map read
        # from this tmp registry as production reads the real one: the
        # independent check (amendment G, audit finding 14) compares principals.
        from ledger.hub_policy import PolicyInputs, _registry_principals
        self.policy = PolicyInputs(approver=TEST_POLICY.approver, cosign_effects=TEST_POLICY.cosign_effects,
                                   principal_of=_registry_principals())
        self.roles_dir = install / "templates" / "roles"
        self.roles_dir.mkdir(parents=True)
        for role in ("researcher", "scout"):
            (self.roles_dir / f"{role}.md").write_text(f"# {role}\n\nA fake role description for tests.\n")
        monkeypatch.setattr(hubmod, "ROLES_DIR", self.roles_dir)
        self.clock = Clock(START)
        # Every HLC stamp (and the log's skew guard) follows the test clock, so
        # "the day" of a spend is the hub's day and a simulated midnight is real.
        import time as _time
        import ledger.log as logmod
        fake = types.SimpleNamespace(**{k: getattr(_time, k) for k in dir(_time) if not k.startswith("_")})
        fake.time = lambda: self.clock() / 1000
        monkeypatch.setattr(hlc, "time", fake)
        monkeypatch.setattr(logmod, "time", fake)
        self.hub_dir = tmp / "hub-space"
        (self.hub_dir / ".datacore" / "events").mkdir(parents=True)
        hubmod.enable(self.hub_dir, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900, owner=OWNER,
                      keys_dir=self.keys_dir, registry_path=self.registry, policy=self.policy)
        if pools:
            hubmod.enable_pools(self.hub_dir)
        self.hubs: list = []
        self._hub = None
        self.t = None

    def hub(self, *, eligibility=None, state: str = "state") -> "hubmod.Hub":
        h = hubmod.Hub(self.hub_dir, GRANTOR, keys_dir=self.keys_dir, registry_path=self.registry,
                       state_dir=self.tmp / state / GRANTOR, clock=self.clock, eligibility=eligibility,
                       policy=self.policy)
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
                                state_dir=self.tmp / "wstate" / actor, clock=self.clock, policy=self.policy)

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


def granted_role(env, name: str, template: str) -> dict:
    """What a grant names for a role: the description to load and, pinned to the
    attempt, the content hash of that file (amendment G, audit finding 14)."""
    import hashlib
    data = (env.roles_dir / Path(template).name).read_bytes()
    return {"name": name, "template": template, "sha256": hashlib.sha256(data).hexdigest()}


def refused(fn, *args) -> "hubmod.HubRefused":
    with pytest.raises(hubmod.HubRefused) as exc:
        fn(*args)
    return exc.value


def submit_raw(hub, writer, etype: str, payload: dict) -> dict:
    """Append to the writer's own log and hand it to the hub, skipping the writer's
    pre-check. A result names the writer's lease attempt, as HubWriter.append does."""
    if etype in ("item.complete", "item.release"):
        payload = {**payload, **{k: v for k, v in (writer._attempt_for(payload.get("id")) or {}).items() if v}}
    raw = writer.log.append(etype, payload)
    return hub.append({"event": json.loads(to_line(raw)), "log": writer.actor})


# ── 1. a task addressed to a named agent (the main scenario) ────────────────

def test_an_addressed_task_is_leased_only_by_its_agent_and_names_its_role(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    hub.set_budget("researcher", 0)                                          # a closed pool
    own.append("item.create", {"id": "mine", "title": "addressed", "assignee": "wa", "role": "researcher"})
    own.append("item.create", {"id": "plain", "title": "addressed, no role", "assignee": "wa"})

    assert refused(wb.request_lease, "mine").code == "not_eligible"          # another agent
    grant = wa.request_lease("mine")
    assert grant["status"] == "granted"                                      # the pool's budget is not its
    assert grant["lease"]["payload"]["role"] == granted_role(env, "researcher", RESEARCHER)
    assert wa.request_lease("plain")["status"] == "granted"
    # Addressed work needs no DONE_WHEN and its own completion counts as before.
    wa.append("item.complete", {"id": "mine"})
    wa.append("item.complete", {"id": "plain"})
    assert env.item("mine").status == "completed" and env.item("plain").status == "completed"


# ── 2. an unaddressed task with a role: any available agent ─────────────────

def test_any_registered_agent_may_lease_an_unaddressed_role_task_one_at_a_time(env):
    hub = env.start()
    own, wa, wb, wc = env.writer("own"), env.writer("wa"), env.writer("wb"), env.writer("wc")
    pool_item(own, "p1")
    pool_item(own, "p2")
    pool_item(own, "s1", role="scout")

    grant = wa.request_lease("p1")                                           # no membership grant needed
    assert grant["status"] == "granted"
    assert grant["lease"]["payload"]["role"] == granted_role(env, "researcher", RESEARCHER)
    assert refused(wb.request_lease, "p1").code == "taken"                   # one lease at a time
    assert wb.request_lease("p2")["status"] == "granted"
    assert wc.request_lease("s1")["status"] == "granted"                     # any role, any agent
    assert env.item("p1").lease_holder == "wa" and env.item("p2").lease_holder == "wb"
    assert not hasattr(hub, "record_role")


def test_only_a_registered_agent_principal_is_available(env):
    env.start()
    own, wz = env.writer("own"), env.writer("wz")
    pool_item(own, "p1")
    err = refused(own.request_lease, "p1")                                   # a human principal
    assert err.code == "not_eligible" and "agent" in err.message
    err = refused(wz.request_lease, "p1")                                    # signed, but no principal
    assert err.code == "not_eligible" and "agent" in err.message
    assert env.item("p1").lease_holder is None


# ── 3. the role description is named and must exist ─────────────────────────

def test_a_role_without_a_template_is_refused_and_checked_at_lease_time(env):
    env.start()
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "p1", role="astronaut")
    own.append("item.create", {"id": "mine", "title": "addressed", "assignee": "wa", "role": "astronaut"})
    pool_item(own, "bad", role="../../etc/passwd")
    for iid in ("p1", "mine"):
        err = refused(wa.request_lease, iid)
        assert err.code == "unknown_role" and "astronaut" in err.message, (iid, err.code)
    assert refused(wa.request_lease, "bad").code == "unknown_role"           # not a role name at all
    # The template appears later: the next request finds it.
    (env.roles_dir / "astronaut.md").write_text("# astronaut\n")
    grant = wa.request_lease("p1")
    assert grant["lease"]["payload"]["role"]["template"] == ".datacore/templates/roles/astronaut.md"


def test_a_role_with_only_the_base_layer_names_the_base_file(env):
    """Role templates are layered context: `<role>.md` is composed from
    `<role>.base.md` (DIP-0002). Before the composed file is built, the grant
    names the base layer, the file that exists."""
    env.start()
    own, wa = env.writer("own"), env.writer("wa")
    (env.roles_dir / "dev.base.md").write_text("# dev\n")
    pool_item(own, "d1", role="dev")
    assert wa.request_lease("d1")["lease"]["payload"]["role"] == granted_role(
        env, "dev", ".datacore/templates/roles/dev.base.md")


# ── 4. no assignee and no role: still not dispatched ────────────────────────

def test_an_unaddressed_task_without_a_role_is_not_dispatched(env):
    env.start()
    own, wa = env.writer("own"), env.writer("wa")
    own.append("item.create", {"id": "nobody", "title": "unaddressed", "done_when": DONE})
    err = refused(wa.request_lease, "nobody")
    assert err.code == "not_eligible" and "nobody" in err.message


# ── 5. budget, DONE_WHEN and an independent check ───────────────────────────

def test_daily_budget_stops_new_leases_keeps_existing_ones_and_resets_next_day(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    hub.set_budget("researcher", 500)
    for i in range(4):
        pool_item(own, f"p{i}")

    assert wa.request_lease("p0")["status"] == "granted"
    wa.append("spend.record", {"item": "p0", "cents": 300, "model": "test"})
    assert wb.request_lease("p1")["status"] == "granted"                    # 300 < 500: still open
    wb.append("spend.record", {"item": "p1", "cents": 250, "model": "test"})

    # 550 >= 500: the cap is reached. No NEW lease, for any agent ...
    assert refused(wa.request_lease, "p2").code == "budget_reached"
    assert refused(env.writer("wc").request_lease, "p2").code == "budget_reached"
    # ... but the existing leases carry on: renew, spend, complete.
    env.clock.advance(300)
    assert wb.renew_lease("p1")["status"] == "renewed"
    wb.append("spend.record", {"item": "p1", "cents": 50, "model": "test"})
    wb.append("item.complete", {"id": "p1"})
    assert env.item("p1").status == "submitted"            # submitted for review (finding 14)
    assert env.item("p0").lease_holder == "wa"

    # Spending on addressed work does not count against the pool.
    own.append("item.create", {"id": "solo", "title": "addressed", "assignee": "wa", "role": "researcher"})
    st = fold(read_events(env.hub_dir))
    assert st.pool_spend == {"researcher|2027-01-15": 600}
    # The next UTC day resets the count.
    env.clock.advance(DAY_MS / 1000)
    assert wa.request_lease("p2")["status"] == "granted"


def test_a_role_without_a_budget_is_not_capped_and_a_budget_is_per_role(env):
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
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
    assert refused(hub.set_budget, "astronaut", 100).code == "unknown_role"


def test_an_unaddressed_role_task_without_done_when_is_not_leased(env):
    env.start()
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "vague", done_when=None)
    pool_item(own, "org", done_when=None, org={"properties": {"DONE_WHEN": DONE}})
    err = refused(wa.request_lease, "vague")
    assert err.code == "not_eligible" and "DONE_WHEN" in err.message
    assert wa.request_lease("org")["status"] == "granted"                    # the org property counts


def test_the_fold_refuses_a_lease_of_a_pool_task_without_done_when(env):
    """A hub that grants anyway (an override, old code, a hand-written lease)
    does not make the lease real: every copy folds it to a recorded conflict."""
    env.start(eligibility=allow_any)
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "vague", done_when=None)
    assert wa.request_lease("vague")["status"] == "granted"                  # the hub said yes ...
    item = env.item("vague")
    assert item.lease_holder is None and item.status == "created", item.history   # ... the fold says no
    assert any("DONE_WHEN" in why for why in item.lease_conflicts.values())
    copy = env.tmp / "copy"
    (copy / ".datacore" / "events").mkdir(parents=True)
    git_pull(env.hub_dir, copy)
    assert fold(read_events(copy)).state_root() == fold(read_events(env.hub_dir)).state_root()


def test_completion_is_accepted_only_after_another_actors_check(env):
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    pool_item(own, "p1")
    wa.request_lease("p1")
    wa.append("item.complete", {"id": "p1", "result": "summary attached"})
    item = env.item("p1")
    assert item.status == "submitted" and not pool_accepted(item)            # submitted for review, not accepted

    # The holder's own check is refused: the writer refuses before writing ...
    before = (wa.space_dir / ".datacore" / "events" / "wa.jsonl").read_bytes()
    assert refused(wa.append, "item.verify", {"id": "p1"}).code == "own_check"
    assert (wa.space_dir / ".datacore" / "events" / "wa.jsonl").read_bytes() == before
    # ... and a raw holder check that reaches the hub anyway is refused at the
    # front door (the policy rule, hub_policy.authorize) and changes nothing.
    err = refused(submit_raw, hub, wa, "item.verify", {"id": "p1"})
    assert err.code == "not_authorized" and "own_check" in err.message
    item = env.item("p1")
    assert item.status == "submitted" and not pool_accepted(item), item.history

    # A different agent's check is what accepts it (the checker reads the work first).
    git_pull(env.hub_dir, wb.space_dir)
    wb.append("item.verify", {"id": "p1", "check": DONE})
    item = env.item("p1")
    assert item.status == "verified" and pool_accepted(item)


def test_the_fold_refuses_a_pool_completion_when_done_when_was_removed(env):
    """DONE_WHEN is needed at completion too, not only at the lease."""
    hub = env.start()
    own, wa = env.writer("own"), env.writer("wa")
    pool_item(own, "p1")
    wa.request_lease("p1")
    # Under a live attempt only the approver changes DONE_WHEN (Codex re-audit,
    # new defect 5); the task's owner writer is refused, so the approver removes it.
    assert refused(own.append, "item.update", {"id": "p1", "done_when": ""}).code == "not_authorized"
    env.writer(APPROVER).append("item.update", {"id": "p1", "done_when": ""})
    git_pull(env.hub_dir, wa.space_dir)
    assert refused(wa.append, "item.complete", {"id": "p1"}).code == "no_done_when"
    err = refused(submit_raw, hub, wa, "item.complete", {"id": "p1"})
    assert err.code == "not_authorized" and "no_done_when" in err.message
    item = env.item("p1")
    assert item.status == "claimed"
    # A copy that has the completion anyway (it came by git) folds it to nothing.
    raw = wa.log.path
    copy = env.tmp / "copy"
    (copy / ".datacore" / "events").mkdir(parents=True)
    git_pull(env.hub_dir, copy)
    (copy / ".datacore" / "events" / raw.name).write_bytes(raw.read_bytes())
    item = fold(read_events(copy)).items["p1"]
    assert item.status == "claimed" and any("DONE_WHEN" in h for h in item.history)


# ── 6. off by default; old roots; no membership event ───────────────────────

def test_pools_are_off_unless_the_space_opts_in(tmp_path, monkeypatch):
    e = PoolEnv(tmp_path, monkeypatch, pools=False)
    try:
        assert hubmod.load_config(e.hub_dir).pools is False
        hub = e.start()
        own, wa = e.writer("own"), e.writer("wa")
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


def test_membership_is_gone_and_the_budget_is_written_only_by_the_hub(env):
    hub = env.start()
    wa = env.writer("wa")
    assert "member.role" not in EVENT_TYPES
    e = wa.log.append("pool.budget", {"role": "researcher", "cents_per_day": 10**9})
    assert refused(hub.append, {"event": json.loads(to_line(e))}).code == "hub_only_type"


def test_folding_a_log_without_pool_events_hashes_as_before():
    """Old logs never contained pool.budget or hub-stamped pool spend: the new
    state fields must not enter their state root."""
    def ev(seq, etype, payload, actor="own"):
        body = body_dict(seq, f"{1_700_000_000_000 + seq:013d}.0000.{actor}", actor, etype, payload, "x")
        return Event(**body, hash=compute_hash(body), sig="")
    events = [ev(0, "item.create", {"id": "t", "title": "x", "role": "researcher"}),
              ev(1, "spend.record", {"item": "t", "cents": 40})]
    st = fold(events)
    assert st.budgets == {} and st.pool_spend == {}
    assert not hasattr(st, "members")
    from ledger.fold import LedgerState
    legacy = LedgerState(items=st.items, spend=st.spend, orphans=st.orphans)
    assert st.state_root() == legacy.state_root()


# ── worked example: a research space with the researcher role ───────────────

def test_worked_example_research_space(env):
    """The whole loop for one space, as the owner would run it.

    Owner, once:   python3 -m ledger.hub enable <space> --hub-id research-hub --grantor hubd
                   python3 -m ledger.hub pools <space>
    Hub, as fact:  set_budget("researcher", 2_000)
    Owner adds:    an item with role=researcher, no assignee, and a DONE_WHEN
    Agent wa:      leases it, is told to load the researcher description, spends, completes
    Agent wb:      checks it against the DONE_WHEN -> accepted
    """
    hub = env.start()
    own, wa, wb = env.writer("own"), env.writer("wa"), env.writer("wb")
    hub.set_budget("researcher", 2_000)
    own.append("item.create", {"id": "competitor-scan", "title": "Scan three competitors' pricing pages",
                               "role": "researcher", "done_when": DONE})
    grant = wa.request_lease("competitor-scan")
    assert grant["status"] == "granted" and grant["lease"]["payload"]["role"]["template"] == RESEARCHER
    assert refused(wb.request_lease, "competitor-scan").code == "taken"
    wa.append("spend.record", {"item": "competitor-scan", "cents": 120, "model": "test"})
    wa.append("item.complete", {"id": "competitor-scan", "result": "summary attached"})
    git_pull(env.hub_dir, wb.space_dir)                                      # wb reads the result
    wb.append("item.verify", {"id": "competitor-scan", "check": DONE})
    state = fold(read_events(env.hub_dir))
    assert pool_accepted(state.items["competitor-scan"])
    assert state.pool_spend == {"researcher|2027-01-15": 120}
