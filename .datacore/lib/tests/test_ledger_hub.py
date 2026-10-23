"""The hub as the ledger's front door (DIP-0034, October 2026 amendment, sections A-E, G).

Written before the code (task a6cf76bf). One test per DONE_WHEN line:

  1. two actors request one task; exactly one gets the lease -- in the hub AND
     in the folded state of every copy of the log;
  2. the hub is down for a simulated hour: no event lost, no double run starts,
     buffered local appends replay when it returns;
  3. a tampered hub entry fails the writer's own git-side receipt check;
  4. a hub rebuilt from the log equals live state;
  5. the hub refuses an unsigned or wrongly signed append;
  6. no automatic failover: a second hub instance for the same space refuses to
     grant leases unless explicitly promoted.

Plus the boundary: everything is OFF unless a space opts in, and an event
written without a hub is byte-for-byte what it was before.

Keys are generated in tmp dirs; the real ~/.datacore/keys is never touched.
"""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest

from ledger import hub as hubmod
from ledger.events import Event, body_dict, canonical_bytes, compute_hash, from_line, to_line
from ledger.fold import fold
from ledger.log import EventLog, read_events

HUB_ID = "hub-test-1"
GRANTOR = "hubd"
APPROVER = "boss"
#: The space's owner: signs the hub.enable and hub.promote records (audit finding 4).
OWNER = "owner"


def _test_policy():
    from ledger.hub_policy import PolicyInputs
    return PolicyInputs(approver=APPROVER, cosign_effects=frozenset({"payment"}))


TEST_POLICY = None      # set below, once ledger.hub_policy exists (tests are written first)
try:
    TEST_POLICY = _test_policy()
except ImportError:
    pass


# ── fixtures ────────────────────────────────────────────────────────────────

class Clock:
    """The hub's and the writers' wall clock, in ms, moved by hand."""

    def __init__(self, start: int = 1_800_000_000_000):
        self.now = start

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += int(seconds * 1000)


def git_pull(src: Path, dst: Path) -> None:
    """Simulate `git pull` of the published log: every events file in `src`
    lands in `dst` (append-only files, so the longer copy is the newer one)."""
    for sub in ("events", "telemetry"):
        s = src / ".datacore" / sub
        if not s.is_dir():
            continue
        d = dst / ".datacore" / sub
        d.mkdir(parents=True, exist_ok=True)
        for f in s.glob("*.jsonl"):
            t = d / f.name
            if not t.exists() or len(f.read_bytes()) >= len(t.read_bytes()):
                shutil.copyfile(f, t)
    cfg = src / hubmod.CONFIG
    if cfg.exists():
        (dst / hubmod.CONFIG).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cfg, dst / hubmod.CONFIG)


def allow_any(item, actor, state):
    """The step-5 slot: a pool that any actor may draw from (roles come later)."""
    return None


class Env:
    def __init__(self, tmp: Path, monkeypatch):
        import ledger.keys as keys
        self.tmp = tmp
        self.keys_dir = tmp / "keys"
        self.registry = tmp / "registry.yaml"
        # No principals.yaml: verification falls back to the tmp registry.
        monkeypatch.setattr(keys, "DATACORE_ROOT", tmp / "root")
        monkeypatch.setenv("DATACORE_ACTOR", "hubtest-host")
        monkeypatch.delenv("DATACORE_SEQUENCER", raising=False)
        monkeypatch.delenv("DATACORE_LEDGER_SIGN", raising=False)
        self.clock = Clock()
        self.hub_dir = tmp / "hub-space"
        (self.hub_dir / ".datacore" / "events").mkdir(parents=True)
        # The policy in force at activation is the owner's record (round 8,
        # owner decision 1): the same fixed policy the hubs and writers use.
        hubmod.enable(self.hub_dir, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900, owner=OWNER,
                      keys_dir=self.keys_dir, registry_path=self.registry,
                      **({"policy": TEST_POLICY} if TEST_POLICY is not None else {}))
        self.recorded_policy = TEST_POLICY
        self.hubs: list = []

    def hub(self, actor: str = GRANTOR, space: Path | None = None, *, eligibility=allow_any,
            state: str = "state", policy=None, record_policy: bool = True) -> "hubmod.Hub":
        # A fixed policy, not the installation's file: the hub's front door
        # applies policy (audit finding 12), and a test must not depend on
        # whatever approvals_policy.yaml this machine happens to carry.
        policy = policy if policy is not None else TEST_POLICY
        # In a hub space the policy is the ledger's record (round 8, owner
        # decision 1): a test that runs its hub under another policy has the
        # owner record that change first. `record_policy=False` starts the hub
        # with a policy nobody recorded (an edited policy file).
        if (record_policy and policy is not None and space in (None, self.hub_dir)
                and policy != self.recorded_policy):
            hubmod.set_policy(self.hub_dir, policy, by=OWNER, reason="test: the hub runs under this policy",
                              keys_dir=self.keys_dir, registry_path=self.registry)
            self.recorded_policy = policy
        h = hubmod.Hub(space or self.hub_dir, actor, keys_dir=self.keys_dir, registry_path=self.registry,
                       state_dir=self.tmp / state / actor, clock=self.clock, eligibility=eligibility,
                       **({"policy": policy} if policy is not None else {}))
        self.hubs.append(h)
        return h

    def writer(self, actor: str, transport) -> "hubmod.HubWriter":
        space = self.tmp / f"{actor}-space"
        if not space.exists():
            (space / ".datacore" / "events").mkdir(parents=True)
            git_pull(self.hub_dir, space)
        return hubmod.HubWriter(space, actor, transport, keys_dir=self.keys_dir,
                                registry_path=self.registry, state_dir=self.tmp / "wstate" / actor,
                                clock=self.clock, policy=TEST_POLICY)

    def close(self):
        for h in self.hubs:
            h.close()


@pytest.fixture
def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    e.close()


def leases_in(space: Path) -> list[Event]:
    return [e for e in read_events(space) if e.type == "item.lease"]


# ── 1. one task, two actors, one lease ──────────────────────────────────────

def test_two_actors_request_one_task_exactly_one_gets_the_lease(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, a, b = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "pool task"})
    for w in (a, b):
        git_pull(env.hub_dir, w.space_dir)

    results: dict = {}
    barrier = threading.Barrier(2)

    def go(w):
        barrier.wait()
        try:
            results[w.actor] = w.request_lease("t1")
        except hubmod.HubRefused as exc:
            results[w.actor] = exc

    threads = [threading.Thread(target=go, args=(w,)) for w in (a, b)]
    [th.start() for th in threads]
    [th.join() for th in threads]

    granted = [k for k, v in results.items() if isinstance(v, dict) and v["status"] == "granted"]
    refused = [v for v in results.values() if isinstance(v, hubmod.HubRefused)]
    assert len(granted) == 1, results
    assert len(refused) == 1 and refused[0].code == "taken"
    winner = granted[0]

    # In the hub: one lease event, held by the winner, signed by the hub.
    assert [e.payload["holder"] for e in leases_in(env.hub_dir)] == [winner]
    assert hub.status()["leases"]["t1"]["holder"] == winner

    # In the folded state of EVERY copy of the log.
    for w in (owner, a, b):
        git_pull(env.hub_dir, w.space_dir)
    for space in (env.hub_dir, owner.space_dir, a.space_dir, b.space_dir):
        item = fold(read_events(space)).items["t1"]
        assert item.lease_holder == winner and item.status == "claimed" and item.owner == winner


def test_fold_rule_earliest_lease_wins_and_the_losers_completion_is_a_recorded_conflict(env):
    """Partition + promotion produces two overlapping leases. Every copy folds
    the same winner, and the loser's completion is recorded, never a no-op."""
    hub_a = env.hub()
    ta = hubmod.LocalTransport(hub_a)
    owner = env.writer("own", ta)
    owner.append("item.create", {"id": "t1", "title": "pool task"})
    wa = env.writer("wa", ta)
    # Partition: a second checkout taken BEFORE hub_a's lease (it never saw
    # it) is promoted by the owner (an explicit act) and grants the same item
    # to wb. (Round 8: an attested promotion needs the old grantor's log as
    # far as the copy shows it served, so the copy keeps hub_a's start record.)
    b_dir = env.tmp / "hub-b-space"
    shutil.copytree(env.hub_dir, b_dir)
    lease_a = wa.request_lease("t1")
    # Since audit finding 4 the promotion is the owner's signed record and the
    # new grantor waits out every lease the old one could have granted. Only a
    # wrong clock gets a second, overlapping grant out of it: the owner's clock
    # here is an hour behind, so the wait has already passed on the hub's.
    hubmod.promote(b_dir, "hubb", by=OWNER, reason="test partition", keys_dir=env.keys_dir,
                   registry_path=env.registry, attest_stopped=True, clock=lambda: env.clock() - 3_600_000)
    hub_b = env.hub("hubb", b_dir)
    env.clock.advance(1)
    wb_dir = env.tmp / "wb-space"
    shutil.copytree(b_dir, wb_dir)
    wb = hubmod.HubWriter(wb_dir, "wb", hubmod.LocalTransport(hub_b), keys_dir=env.keys_dir,
                          registry_path=env.registry, state_dir=env.tmp / "wstate" / "wb", clock=env.clock)
    lease_b = wb.request_lease("t1")
    assert lease_a["status"] == lease_b["status"] == "granted"

    wa.append("item.complete", {"id": "t1"})
    wb.append("item.complete", {"id": "t1"})

    # The partition heals: every copy gets every log, in different orders.
    merged = []
    for order in ((env.hub_dir, b_dir, wa.space_dir, wb.space_dir), (wb.space_dir, wa.space_dir, b_dir, env.hub_dir)):
        dst = env.tmp / f"merged-{len(merged)}"
        (dst / ".datacore" / "events").mkdir(parents=True)
        for src in order:
            git_pull(src, dst)
        merged.append(dst)
    items = [fold(read_events(d)).items["t1"] for d in merged]
    roots = {fold(read_events(d)).state_root() for d in merged}
    assert len(roots) == 1
    for item in items:
        assert item.lease_holder == "wa", item.history     # the earlier lease
        assert item.status == "completed" and item.owner == "wa"
        reasons = " | ".join(item.lease_conflicts.values())
        assert "overlap" in reasons and "wb" in reasons    # the losing lease
        assert any("wb" in r and "not hold" in r for r in item.lease_conflicts.values())
        assert not any(h.endswith("no-op (illegal transition from status=completed)") for h in item.history)


def test_fold_orders_one_hubs_leases_by_hub_sequence_not_list_order(env):
    def lease(seq: int, holder: str, granted: int) -> Event:
        body = body_dict(seq, f"{1_800_000_000_000 + 10 - seq:013d}.0000.{GRANTOR}", GRANTOR, "item.lease",
                         {"id": "t1", "holder": holder, "granted_ms": granted, "expires_ms": granted + 900_000},
                         "x", hub=HUB_ID)
        return Event(**body, hash=compute_hash(body), sig="")

    create_body = body_dict(0, "1700000000000.0000.own", "own", "item.create", {"id": "t1"}, "GENESIS")
    create = Event(**create_body, hash=compute_hash(create_body), sig="")
    later, earlier = lease(5, "wb", 1_800_000_000_500), lease(3, "wa", 1_800_000_000_400)
    for events in ([create, later, earlier], [create, earlier, later]):
        item = fold(events).items["t1"]
        assert item.lease_holder == "wa" and item.owner == "wa", item.history
        assert later.hash in item.lease_conflicts


def test_default_eligibility_keeps_the_assignee_rule(env):
    hub = env.hub(eligibility=None)      # default: the assignee rule, no pools yet
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "nobody", "title": "unaddressed"})
    owner.append("item.create", {"id": "pool", "title": "pool", "role": "researcher"})
    owner.append("item.create", {"id": "mine", "title": "addressed", "assignee": "wa"})
    for item in ("nobody", "pool"):
        with pytest.raises(hubmod.HubRefused) as exc:
            wa.request_lease(item)
        assert exc.value.code == "not_eligible"
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("mine")
    assert exc.value.code == "not_eligible"
    assert wa.request_lease("mine")["status"] == "granted"


# ── 2. an hour without the hub ──────────────────────────────────────────────

def test_hub_down_for_an_hour_loses_nothing_starts_no_double_run_and_replays(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "pool task"})
    assert wa.request_lease("t1")["status"] == "granted"

    env.clock.advance(60)
    t.down = True
    hub.close()                                         # the process is gone

    # Addressed work continues: local appends succeed and are buffered.
    env.clock.advance(4 * 60)
    _, receipt = wa.append("item.update", {"id": "t1", "note": "progress"})
    assert receipt is None
    wa.append("item.create", {"id": "t2", "title": "follow-up", "assignee": "wa"})
    wa.append("item.complete", {"id": "t1"})
    assert wa.outage()["since_ms"] is not None

    # Pool leasing pauses: wb cannot take anything, so nothing starts twice.
    for _ in range(3):
        env.clock.advance(15 * 60)
        with pytest.raises(hubmod.HubUnavailable):
            wb.request_lease("t1")

    # The hub returns an hour later. wb asks BEFORE wa has replayed: wa's lease
    # expired while nobody could renew it. Since audit finding 10 that is the
    # recovery barrier (this hub restarted without its request state), not a
    # fixed restart grace; either way nothing is granted.
    env.clock.advance(60)
    hub2 = env.hub(state="state2")
    t.hub, t.down = hub2, False
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code in ("restart_grace", "taken", "recovering", "uncertain_attempt")

    replayed = wa.replay()
    assert [r["status"] for r in replayed] == ["accepted"] * 3
    assert wa.outage() is None
    # Still nothing for wb: the restarted hub lost its request state, so the
    # barrier holds until the owner clears it (Codex re-audit, new defect 3);
    # then wa's completion, admitted after its lease ran out, is the owner's
    # to decide (Codex re-audit, finding 9), not a completion.
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code == "recovering"
    hub2.clear_recovery(hubmod.sign_request(OWNER, "recovery.clear", {"hub": HUB_ID, "space": _space_id(env.hub_dir)},
                                            keys_dir=env.keys_dir, clock=env.clock))
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code == "owner_resolution"

    # No event lost: the hub's copy of wa's log is wa's log, line for line.
    local = (wa.space_dir / ".datacore" / "events" / "wa.jsonl").read_bytes()
    assert (env.hub_dir / ".datacore" / "events" / "wa.jsonl").read_bytes() == local
    # No double run: one lease, one completion recorded (for the owner), no conflict.
    assert [e.payload["holder"] for e in leases_in(env.hub_dir)] == ["wa"]
    item = fold(read_events(env.hub_dir)).items["t1"]
    assert item.status != "completed" and len(item.lease_unresolved) == 1 and not item.lease_conflicts


# ── 3. the writer's own receipt ─────────────────────────────────────────────

def _published(env, hub, writers) -> Path:
    hub.seal()
    git = env.tmp / f"git-{len(list(env.tmp.glob('git-*')))}"
    (git / ".datacore" / "events").mkdir(parents=True)
    git_pull(env.hub_dir, git)
    return git


def _rewrite(path: Path, actor: str, keys_dir: Path, edit) -> None:
    """A dishonest hub edits one event, then re-hashes and re-signs the rest of
    its chain with whatever key it holds."""
    from ledger.keys import sign
    out, prev = [], None
    for i, line in enumerate(path.read_text().splitlines()):
        e = from_line(line)
        e = edit(e) or e
        body = body_dict(e.seq, e.hlc, e.actor, e.type, e.payload, prev if prev else e.prev, hub=e.hub)
        h = compute_hash(body)
        sig = sign(actor, canonical_bytes(body), keys_dir=keys_dir) if actor else e.sig
        out.append(to_line(Event(**body, hash=h, sig=sig)))
        prev = h
    path.write_text("\n".join(out) + "\n")


def _reseal(env, git: Path) -> None:
    """Drop the old seal and seal the tampered log again with the hub's key.

    The dishonest hub seals directly with its key. It cannot use the honest
    `Hub.seal`: since audit finding 2 that refuses to admit -- and so to seal --
    a writer event whose signature no longer verifies."""
    from ledger.seal import build_seal_payload
    hub_log = git / ".datacore" / "events" / f"{GRANTOR}.jsonl"
    lines = [ln for ln in hub_log.read_text().splitlines() if from_line(ln).type != "ledger.seal"]
    hub_log.write_text("\n".join(lines) + "\n")
    EventLog(git, GRANTOR, keys_dir=env.keys_dir, registry_path=env.registry, sign=True).append(
        "ledger.seal", build_seal_payload(read_events(git)))


def test_tampered_hub_entry_fails_the_writers_receipt_check(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "pool task"})
    wa.request_lease("t1")
    wa.append("item.complete", {"id": "t1", "result": "42"})

    git = _published(env, hub, [owner, wa])
    honest = wa.receipt_check(git, hub_actor=GRANTOR)
    assert honest["ok"] is True, honest

    # (a) the hub alters the writer's result and re-seals: the seal is
    # self-consistent, the writer's own signature is not.
    a = env.tmp / "tamper-a"
    shutil.copytree(git, a)
    _rewrite(a / ".datacore" / "events" / "wa.jsonl", "", env.keys_dir,
             lambda e: Event(**{**e.__dict__, "payload": {**e.payload, "result": "41"}})
             if e.type == "item.complete" else None)
    _reseal(env, a)
    # The re-made seal is self-consistent: it names the tampered state's root.
    from ledger.seal import as_sequencer, latest_seal
    tampered = read_events(a)
    with as_sequencer(GRANTOR):
        resealed = latest_seal(tampered)
    assert resealed.state_root == fold([e for e in tampered if e.type != "ledger.seal"]).state_root()
    report = wa.receipt_check(a, hub_actor=GRANTOR)
    assert report["ok"] is False and report["problems"], report

    # (b) the hub rewrites its own lease to another holder, re-signed with the
    # hub key and re-sealed: the writer's receipt from the first confirmation
    # no longer matches.
    b = env.tmp / "tamper-b"
    shutil.copytree(git, b)
    _rewrite(b / ".datacore" / "events" / f"{GRANTOR}.jsonl", GRANTOR, env.keys_dir,
             lambda e: Event(**{**e.__dict__, "payload": {**e.payload, "holder": "intruder"}})
             if e.type == "item.lease" else None)
    _reseal(env, b)
    report = wa.receipt_check(b, hub_actor=GRANTOR)
    assert report["ok"] is False and any("lease" in p for p in report["problems"]), report


# ── 4. rebuilt from the log ─────────────────────────────────────────────────

def test_hub_rebuilt_from_the_log_equals_live_state(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    for i in range(3):
        owner.append("item.create", {"id": f"t{i}", "title": f"task {i}"})
    wa.request_lease("t0")
    wb.request_lease("t1")
    env.clock.advance(300)
    wb.renew_lease("t1")
    wa.append("item.complete", {"id": "t0"})
    wb.append("item.complete", {"id": "t0"})                # not the holder: a conflict
    live = hub.snapshot()
    assert live["leases"]["t1"]["holder"] == "wb"
    assert live["conflicts"]

    git = _published(env, hub, [owner, wa, wb])
    live = hub.snapshot()
    rebuilt = env.hub(space=git, state="rebuilt")
    assert rebuilt.snapshot() == live


# ── 5. signed or refused ────────────────────────────────────────────────────

def test_hub_refuses_unsigned_or_wrongly_signed_appends(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    wa = env.writer("wa", t)
    wa.append("item.create", {"id": "ok", "title": "signed"})        # the honest path works

    # Unsigned: an EventLog with signing off, in a scratch copy.
    scratch = env.tmp / "scratch"
    (scratch / ".datacore" / "events").mkdir(parents=True)
    unsigned = EventLog(scratch, "wx", sign=False, hub=HUB_ID).append("item.create", {"id": "u"})
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.append({"event": json.loads(to_line(unsigned))})
    assert exc.value.code == "unsigned"

    # Wrongly signed: wb's key signs an event that claims to be wa's next one.
    EventLog(scratch, "wb", keys_dir=env.keys_dir, registry_path=env.registry, sign=True, hub=HUB_ID)
    tail = from_line((wa.space_dir / ".datacore" / "events" / "wa.jsonl").read_text().splitlines()[-1])
    from ledger.keys import sign
    body = body_dict(tail.seq + 1, f"{env.clock() + 5:013d}.0000.wa", "wa", "item.create",
                     {"id": "forged"}, tail.hash, hub=HUB_ID)
    forged = Event(**body, hash=compute_hash(body), sig=sign("wb", canonical_bytes(body), keys_dir=env.keys_dir))
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.append({"event": json.loads(to_line(forged))})
    assert exc.value.code == "bad_signature"

    # Correctly signed but edited after signing.
    good = wa.log.append("item.create", {"id": "edited"})
    edited = json.loads(to_line(good))
    edited["payload"]["id"] = "edited-later"
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.append({"event": edited})
    assert exc.value.code in ("bad_signature", "bad_hash")

    # Hub-only types and another hub's events are refused too.
    lease = wa.log.append("item.lease", {"id": "ok", "holder": "wa", "granted_ms": 0, "expires_ms": 1})
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.append({"event": json.loads(to_line(lease))})
    assert exc.value.code == "hub_only_type"
    assert [e.payload["id"] for e in read_events(env.hub_dir) if e.actor == "wa"] == ["ok"]

    # A lease request has to be signed by the actor it names.
    req = hubmod.sign_request("wb", "lease.request", {"hub": HUB_ID, "item": "ok", "holder": "wa"},
                              keys_dir=env.keys_dir, clock=env.clock)
    req["actor"] = "wa"
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.request_lease(req)
    assert exc.value.code == "bad_signature"


# ── 6. no automatic failover ────────────────────────────────────────────────

def test_second_hub_refuses_to_grant_until_explicitly_promoted(env):
    primary = env.hub()
    t = hubmod.LocalTransport(primary)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "pool task"})
    owner.append("item.create", {"id": "t2", "title": "pool task 2"})

    standby_dir = env.tmp / "standby-space"
    shutil.copytree(env.hub_dir, standby_dir)
    standby = env.hub("hubstandby", standby_dir)
    st = hubmod.LocalTransport(standby)
    wa_on_standby = hubmod.HubWriter(wa.space_dir, "wa", st, keys_dir=env.keys_dir, registry_path=env.registry,
                                     state_dir=env.tmp / "wstate" / "wa", clock=env.clock)
    with pytest.raises(hubmod.HubRefused) as exc:
        wa_on_standby.request_lease("t1")
    assert exc.value.code == "not_active_hub"

    # The primary going dark does not change that: nothing fails over.
    t.down = True
    primary.close()
    with pytest.raises(hubmod.HubUnavailable):
        wa.request_lease("t1")
    with pytest.raises(hubmod.HubRefused) as exc:
        wa_on_standby.request_lease("t1")
    assert exc.value.code == "not_active_hub"
    assert not hasattr(hubmod, "failover") and not hasattr(hubmod.Hub, "failover")

    # A second instance of the SAME hub on one host is locked out.
    twin_a = env.hub("hubstandby", standby_dir, state="state")
    with pytest.raises(hubmod.HubRefused) as exc:
        twin_a.request_lease(hubmod.sign_request("wa", "lease.request", {"hub": HUB_ID, "item": "t1"},
                                                 keys_dir=env.keys_dir, clock=env.clock))
    assert exc.value.code == "locked"
    twin_a.close()

    # The owner promotes the standby: one explicit command, recorded in the config.
    # Since audit finding 4: an owner-signed ledger record, after the owner
    # attests the closed primary is stopped, and the standby waits out one
    # lease period plus the clock uncertainty before it grants.
    cfg = hubmod.promote(standby_dir, "hubstandby", by="owner", reason="primary retired", keys_dir=env.keys_dir,
                         registry_path=env.registry, attest_stopped=True, clock=env.clock)
    assert cfg.grantor == "hubstandby" and cfg.promoted_by == "owner"
    env.clock.advance(900 + 120)
    # The writer learns of the promotion the way every copy does: it pulls the
    # committed record and config. Until then it refuses the new grantor's
    # answers (audit finding 5: first answers are pinned to the grantor it knows).
    git_pull(standby_dir, wa.space_dir)
    assert wa_on_standby.request_lease("t1")["status"] == "granted"


# ── boundary: off by default, old events untouched ──────────────────────────

def test_everything_is_off_unless_the_space_opts_in(tmp_path, monkeypatch):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DATACORE_ROOT", tmp_path / "root")
    monkeypatch.setenv("DATACORE_ACTOR", "hubtest-host")
    space = tmp_path / "plain"
    (space / ".datacore" / "events").mkdir(parents=True)
    assert hubmod.load_config(space) is None
    with pytest.raises(hubmod.HubRefused) as exc:
        hubmod.Hub(space, GRANTOR, keys_dir=tmp_path / "k", registry_path=tmp_path / "r.yaml",
                   state_dir=tmp_path / "s")
    assert exc.value.code == "hub_off"

    e = EventLog(space, "plainw", sign=False).append("item.create", {"id": "x"})
    line = (space / ".datacore" / "events" / "plainw.jsonl").read_text().strip()
    assert "hub" not in json.loads(line)
    assert e.hash == compute_hash({"seq": e.seq, "hlc": e.hlc, "actor": e.actor, "type": e.type,
                                   "payload": e.payload, "prev": e.prev})
    assert from_line(line) == e and to_line(from_line(line)) == line


def test_a_hub_space_stamps_its_hub_and_will_not_write_unsigned(env):
    signed = EventLog(env.hub_dir, "wz", keys_dir=env.keys_dir, registry_path=env.registry, sign=True)
    e = signed.append("item.create", {"id": "z"})
    assert e.hub == HUB_ID and json.loads(to_line(e))["hub"] == HUB_ID
    with pytest.raises(hubmod.HubSigningRequired):
        EventLog(env.hub_dir, "wy", sign=False).append("item.create", {"id": "y"})


def test_a_writer_whose_clock_is_behind_still_completes_after_its_lease(env, monkeypatch):
    """The writer has not pulled the hub's log yet, so its own causal floor
    cannot see the lease; the grant's stamp is the floor instead. Without it a
    slow writer clock files the completion BEFORE the lease, and it folds to
    nothing."""
    import time as _time
    import ledger.hlc as hlc
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "pool task"})
    wa.request_lease("t1")
    real = _time.time
    monkeypatch.setattr(hlc.time, "time", lambda: real() - 30)       # wa's clock is 30 s slow
    wa.append("item.complete", {"id": "t1"})
    monkeypatch.setattr(hlc.time, "time", real)
    item = fold(read_events(env.hub_dir)).items["t1"]
    assert item.status == "completed" and item.owner == "wa", item.history


# ════════════════════════════════════════════════════════════════════════════
# AUDIT FIXES, PART 1 (Codex audit 2026-10-18, findings 1, 2, 3, 5, 6, 7, 16, 18)
#
# Report: 2-datacore/1-tracks/engineering/reports/
#         2026-10-18-dip-0034-amendment-codex-audit-report.md
# Each test reproduces the audit's failure scenario and was seen failing
# before the fix.
# ════════════════════════════════════════════════════════════════════════════

def _raw_event(seq: int, hlc: str, actor: str, type: str, payload: dict, prev: str, hub: str = "") -> Event:
    """An UNSIGNED event with exactly the fields given (hash correct)."""
    body = body_dict(seq, hlc, actor, type, payload, prev, hub=hub)
    return Event(**body, hash=compute_hash(body), sig="")


def _write_raw(space: Path, log: str, events: list[Event]) -> None:
    path = space / ".datacore" / "events" / f"{log}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for e in events:
            f.write(to_line(e) + "\n")


def _legacy_space(env, name: str, chain: list[tuple[str, str, dict]]) -> Path:
    """A space whose `own` log holds unsigned events written BEFORE it opted in
    to a hub; then the owner enables the hub (that writes the cutover manifest)."""
    space = env.tmp / name
    (space / ".datacore" / "events").mkdir(parents=True)
    prev, out = "GENESIS", []
    for seq, (hlc, type, payload) in enumerate(chain):
        e = _raw_event(seq, hlc, "own", type, payload, prev)
        out.append(e)
        prev = e.hash
    _write_raw(space, "own", out)
    # Every writer of a space declares protocol support before it is switched on (finding 17).
    hubmod.declare_ready(space, "own", keys_dir=env.keys_dir, registry_path=env.registry)
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900, owner=OWNER,
                  keys_dir=env.keys_dir, registry_path=env.registry)
    return space


def _space_id(space: Path) -> str:
    """The space's immutable id from its hub config ("" before the field existed)."""
    return getattr(hubmod.load_config(space), "space_id", "")


def _real_ms() -> int:
    import time as _t
    return int(_t.time() * 1000)


# ── finding 1: a timestamp never makes an event legacy ──────────────────────

def test_audit1_backdated_unsigned_event_is_refused_over_the_network(env):
    """Token holder, no key: a correctly hashed unsigned seq-0 `item.create` as
    `victim`, stamped before the cutover. Network admission needs a signature."""
    hub = env.hub()
    old = "0000000000001.0000.victim"
    for hub_field in ("", HUB_ID):
        e = _raw_event(0, old, "victim", "item.create", {"id": "planted"}, "GENESIS", hub=hub_field)
        with pytest.raises(hubmod.HubRefused) as exc:
            hub.append({"event": json.loads(to_line(e))})
        assert exc.value.code == "unsigned", exc.value
    assert not (env.hub_dir / ".datacore" / "events" / "victim.jsonl").exists()


def test_audit1_legacy_is_membership_in_the_cutover_manifest_not_a_timestamp(env):
    """Events written before the space opted in stay valid unsigned -- because the
    owner's cutover manifest names their log's head, not because of their stamp.
    An unsigned event added to that log later (a git import), backdated before
    the cutover, is not legacy and confers nothing."""
    now = _real_ms()
    space = _legacy_space(env, "legacy-space", [
        (f"{now - 60_000:013d}.0000.own", "item.create", {"id": "old", "title": "before the hub"}),
    ])
    head = [e for e in read_events(space) if e.actor == "own"][-1]
    planted = _raw_event(1, f"{now - 30_000:013d}.0000.own", "own", "item.create",
                         {"id": "new", "title": "backdated"}, head.hash, hub=HUB_ID)
    _write_raw(space, "own", [planted])
    hub = env.hub(space=space, state="legacy")
    req = lambda item: hubmod.sign_request("wa", "lease.request", {  # noqa: E731
        "hub": HUB_ID, "space": _space_id(space), "item": item},
        keys_dir=env.keys_dir, clock=env.clock)
    from ledger.keys import ensure_keypair
    ensure_keypair("wa", keys_dir=env.keys_dir, registry_path=env.registry)
    assert hub.request_lease(req("old"))["status"] == "granted"
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.request_lease(req("new"))
    assert exc.value.code == "unknown_item"


def test_audit1_an_edited_cutover_manifest_is_refused(env):
    now = _real_ms()
    space = _legacy_space(env, "manifest-space", [
        (f"{now - 60_000:013d}.0000.own", "item.create", {"id": "old"})])
    config = space / hubmod.CONFIG
    data = json.loads(config.read_text())
    data["cutover_logs"]["own"]["seq"] = 99            # claim more history as legacy
    config.write_text(json.dumps(data))
    with pytest.raises(hubmod.HubRefused) as exc:
        env.hub(space=space, state="manifest")
    assert exc.value.code == "bad_manifest"


# ── finding 2: one admission rule for every path, hub authority only for the grantor ─

def test_audit2_a_lease_an_ordinary_writer_wrote_and_git_imported_confers_nothing(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wb = env.writer("own", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "pool task"})
    # wa writes a signed, hub-stamped lease into its OWN log and it arrives by
    # git, not HTTP (simulated: written straight into the hub's checkout).
    forged = EventLog(env.hub_dir, "wa", keys_dir=env.keys_dir, registry_path=env.registry, sign=True)
    lease = forged.append("item.lease", {"id": "t1", "holder": "wa", "granted_ms": env.clock(),
                                         "expires_ms": env.clock() + 900_000})
    assert lease.hub == HUB_ID and lease.sig
    assert "t1" not in hub.status()["leases"]
    assert wb.request_lease("t1")["status"] == "granted"
    # The same rule seals: the forged event is not in settled state.
    sealed = hub.seal()["seal"]["payload"]
    assert sealed["watermarks"].get("wa", -1) < lease.seq


def test_audit2_an_event_signed_with_an_unknown_key_after_cutover_is_not_admitted(env):
    hub = env.hub()
    ghost = EventLog(env.hub_dir, "ghost", keys_dir=env.tmp / "ghost-keys",
                     registry_path=env.tmp / "ghost-registry.yaml", sign=True)
    ghost.append("item.create", {"id": "g", "title": "from a key nobody registered"})
    t = hubmod.LocalTransport(hub)
    wa = env.writer("wa", t)
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.request_lease("g")
    assert exc.value.code == "unknown_item"


def test_audit2_the_fold_honours_leases_only_from_the_grantors_it_is_given(env):
    create = _raw_event(0, "1700000000000.0000.own", "own", "item.create", {"id": "t1"}, "GENESIS")
    bad = _raw_event(0, "1700000000001.0000.wa", "wa", "item.lease",
                     {"id": "t1", "holder": "wa", "granted_ms": 1, "expires_ms": 2}, "GENESIS", hub=HUB_ID)
    item = fold([create, bad], lease_grantors={HUB_ID: {GRANTOR}}).items["t1"]
    assert item.lease_holder is None and item.status == "created", item.history
    good = _raw_event(0, "1700000000002.0000.hubd", GRANTOR, "item.lease",
                      {"id": "t1", "holder": "wb", "granted_ms": 1, "expires_ms": 2}, "GENESIS", hub=HUB_ID)
    assert fold([create, good], lease_grantors={HUB_ID: {GRANTOR}}).items["t1"].lease_holder == "wb"


# ── finding 3: a receipt is confirmed, pending or failed -- never "ok" while pending ─

def test_audit3_a_result_newer_than_the_seal_is_pending_not_ok(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    wa.request_lease("t1")
    git = _published(env, hub, [owner, wa])
    confirmed = wa.receipt_check(git, hub_actor=GRANTOR)
    assert confirmed["status"] == "confirmed" and confirmed["ok"] is True, confirmed
    done, _ = wa.append("item.complete", {"id": "t1", "result": "42"})   # after the seal
    report = wa.receipt_check(git, hub_actor=GRANTOR)
    assert report["status"] == "pending" and report["ok"] is False, report
    # Asked about one operation, the answer is about that operation's events.
    later = _published(env, hub, [owner, wa])
    wa.append("item.update", {"id": "t1", "note": "after the second seal"})
    assert wa.receipt_check(later, hub_actor=GRANTOR, required=[done.hash])["status"] == "confirmed"
    assert wa.receipt_check(later, hub_actor=GRANTOR)["status"] == "pending"


def test_audit3_a_seal_that_cannot_be_verified_is_not_a_confirmation(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    wa.request_lease("t1")
    wa.append("item.complete", {"id": "t1"})
    git = _published(env, hub, [owner, wa])
    (git / ".datacore" / "events" / "own.jsonl").unlink()   # another writer's covered prefix is missing
    report = wa.receipt_check(git, hub_actor=GRANTOR)
    assert report["ok"] is False and report["status"] in ("pending", "failed"), report


def test_audit3_no_seal_yet_is_pending_and_not_ok(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    wa = env.writer("wa", t)
    wa.append("item.create", {"id": "t1", "title": "task"})
    report = wa.receipt_check(env.hub_dir, hub_actor=GRANTOR)
    assert report["status"] == "pending" and report["ok"] is False, report


# ── finding 5: fresh requests need a current key; answers come from the grantor ─

def _rotate_in_principals(env, actor: str, valid_from_ms: int) -> None:
    """Record an owner-approved rotation of `actor` to a brand-new key, valid
    from `valid_from_ms`; the old key stays valid for the past only."""
    import ledger.keys as keys
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    old = keys._registry_actors(env.registry)[actor]
    new = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    p = keys.principals_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"verify_keys:\n  {actor}: {new}\nverify_key_history:\n  {actor}:\n"
                 f"    - key: {old}\n      valid_from: null\n"
                 f"    - key: {new}\n      valid_from: {valid_from_ms}\n")


def test_audit5_a_retired_key_cannot_sign_a_fresh_lease_request(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    # wa's key was rotated out before "now" on the hub's clock; wa still holds the old one.
    _rotate_in_principals(env, "wa", env.clock() - 60_000)
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.request_lease("t1")
    assert exc.value.code == "bad_signature"


class _Forging(hubmod.LocalTransport):
    """A compromised endpoint: answers lease requests with whatever `forge` returns."""

    def __init__(self, hub, forge):
        super().__init__(hub)
        self.forge = forge

    def request_lease(self, req):
        return self.forge(req, super().request_lease)

    def renew_lease(self, req):
        return self.forge(req, super().renew_lease)


def test_audit5_a_lease_answer_signed_by_another_writer_is_not_a_first_confirmation(env):
    hub = env.hub()
    owner = env.writer("own", hubmod.LocalTransport(hub))
    owner.append("item.create", {"id": "t1", "title": "task"})
    wb = env.writer("wb", hubmod.LocalTransport(hub))      # a registered writer, not the grantor
    fake = wb.log.append("item.lease", {"id": "t1", "holder": "wa", "granted_ms": env.clock(),
                                        "expires_ms": env.clock() + 900_000})
    wa = env.writer("wa", _Forging(hub, lambda req, real: {
        "status": "granted", "lease": json.loads(to_line(fake)), "log": "wb"}))
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.request_lease("t1")
    assert exc.value.code == "bad_lease"
    assert wa.receipts() == []


def test_audit5_a_lease_answer_is_bound_to_its_request_nonce_and_type(env):
    hub = env.hub()
    owner = env.writer("own", hubmod.LocalTransport(hub))
    owner.append("item.create", {"id": "t1", "title": "task"})
    first: dict = {}

    def replay_first(req, real):
        if not first:
            first.update(real(req))
        return dict(first)

    wa = env.writer("wa", _Forging(hub, replay_first))
    assert wa.request_lease("t1")["status"] == "granted"
    with pytest.raises(hubmod.HubRefused) as exc:           # same signed lease, a new request
        wa.request_lease("t1")
    assert exc.value.code == "bad_lease"
    first["status"] = "renewed"
    with pytest.raises(hubmod.HubRefused) as exc:           # a lease offered as a renewal
        wa.renew_lease("t1")
    assert exc.value.code == "bad_lease"
    assert len(wa.receipts()) == 1


# ── finding 6: signatures bind space, hub and log; renewals check the hub ──

def _second_space(env, name: str = "space-b") -> Path:
    space = env.tmp / name
    (space / ".datacore" / "events").mkdir(parents=True)
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900, owner=OWNER,
                  keys_dir=env.keys_dir, registry_path=env.registry)
    return space


def test_audit6_a_signed_event_cannot_be_replayed_into_another_space_or_log(env):
    hub_a = env.hub()
    wa = env.writer("wa", hubmod.LocalTransport(hub_a))
    spend, _ = wa.append("spend.record", {"amount": 5, "currency": "EUR"})
    captured = json.loads(to_line(spend))
    hub_b = env.hub(space=_second_space(env), state="state-b")
    with pytest.raises(hubmod.HubRefused) as exc:
        hub_b.append({"event": captured})                    # same hub id, other space
    assert exc.value.code == "wrong_domain"
    with pytest.raises(hubmod.HubRefused) as exc:
        hub_a.append({"event": captured, "log": "wa-run-2026-10-18"})   # same writer, other log
    assert exc.value.code == "wrong_domain"
    assert not (env.hub_dir / ".datacore" / "events" / "wa-run-2026-10-18.jsonl").exists()


def test_audit6_a_renewal_addressed_to_another_hub_is_refused(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    wa.request_lease("t1")
    space_id = _space_id(env.hub_dir)
    for body in ({"hub": "other-hub", "space": space_id, "item": "t1"},
                 {"hub": HUB_ID, "space": "other-space", "item": "t1"}):
        req = hubmod.sign_request("wa", "lease.renew", body, keys_dir=env.keys_dir, clock=env.clock)
        with pytest.raises(hubmod.HubRefused) as exc:
            hub.renew_lease(req)
        assert exc.value.code in ("wrong_hub", "wrong_space"), exc.value


def test_audit6_lease_receipts_are_kept_per_space_hub_and_log(env):
    hub_a = env.hub()
    t = hubmod.LocalTransport(hub_a)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    wa.request_lease("t1")
    space_b = _second_space(env)
    wa_b = hubmod.HubWriter(space_b, "wa", hubmod.LocalTransport(env.hub(space=space_b, state="state-b")),
                            keys_dir=env.keys_dir, registry_path=env.registry,
                            state_dir=env.tmp / "wstate" / "wa", clock=env.clock)
    assert len(wa.receipts()) == 1
    assert wa_b.receipts() == []


# ── finding 7: clocks are judged at admission, never dropped ───────────────

def test_audit7_malformed_future_and_backwards_clocks_are_refused_at_admission(env, monkeypatch):
    import ledger.log as logmod
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    cases = {
        "wc": lambda first: f"{env.clock() + 3600_000:013d}.0000.wc",            # an hour ahead of the hub
        "wd": lambda first: "garbage.wd",                                         # not an HLC at all
        "we": lambda first: f"{int(first.hlc.split('.')[0]) - 60_000:013d}.0000.we",   # behind its own last event
    }
    for actor, stamp_for in cases.items():
        w = env.writer(actor, t)
        first, receipt = w.append("item.create", {"id": f"{actor}-ok", "title": "a normal first event"})
        assert receipt["status"] == "accepted"
        stamp = stamp_for(first)
        with monkeypatch.context() as m:
            m.setattr(logmod, "tick", lambda a, floor=None: stamp)
            with pytest.raises(hubmod.HubRefused) as exc:
                w.append("item.create", {"id": f"{actor}-skewed", "title": stamp})
        assert exc.value.code == "bad_clock", (stamp, exc.value)
    items = fold(read_events(env.hub_dir)).items
    assert not [i for i in items if i.endswith("-skewed")]


def test_audit7_a_granted_lease_sorts_after_every_event_of_its_item(env):
    """An item whose create is stamped ahead of a later event in the same legacy
    log: the hub's causal floor (sibling tails) misses it, so without a floor
    of its own the lease sorted BEFORE the create and folded as an orphan."""
    now = _real_ms()
    space = _legacy_space(env, "skew-space", [
        (f"{now + 5 * 60_000:013d}.0000.own", "item.create", {"id": "t1", "title": "fast clock"}),
        (f"{now - 60_000:013d}.0000.own", "item.update", {"id": "other", "note": "slow clock"}),
    ])
    hub = env.hub(space=space, state="skew")
    from ledger.keys import ensure_keypair
    ensure_keypair("wa", keys_dir=env.keys_dir, registry_path=env.registry)
    req = hubmod.sign_request("wa", "lease.request", {
        "hub": HUB_ID, "space": _space_id(space), "item": "t1"},
        keys_dir=env.keys_dir, clock=env.clock)
    assert hub.request_lease(req)["status"] == "granted"
    item = fold(read_events(space)).items["t1"]
    assert item.lease_holder == "wa" and item.status == "claimed", item.history


# ── finding 16: the chain is validated under the same lock it is appended under ─

def test_audit16_a_local_append_racing_the_hub_cannot_fork_the_chain(env, monkeypatch):
    import ledger.verify as verify
    hub = env.hub()
    wa = env.writer("wa", hubmod.LocalTransport(hub))
    wa.append("item.create", {"id": "a", "title": "first"})
    pending = wa.log.append("item.create", {"id": "b", "title": "next, not yet submitted"})

    real = verify.check_events
    racer: dict = {}

    def check_events(*args, **kwargs):
        if "thread" not in racer:
            # A cooperating local writer appends to the hub's copy of wa's log
            # under its own file lock while the hub is validating.
            local = EventLog(env.hub_dir, "wa", keys_dir=env.keys_dir, registry_path=env.registry, sign=True)
            racer["thread"] = threading.Thread(target=lambda: local.append("item.create", {"id": "racer"}))
            racer["thread"].start()
            racer["thread"].join(timeout=1.0)
        return real(*args, **kwargs)

    monkeypatch.setattr(verify, "check_events", check_events)
    try:
        hub.append(wa._submission(pending))
    except hubmod.HubRefused:
        pass                                    # refusing is fine; forking is not
    racer["thread"].join()
    monkeypatch.setattr(verify, "check_events", real)
    chain = [from_line(ln) for ln in (env.hub_dir / ".datacore" / "events" / "wa.jsonl").read_text().splitlines()]
    assert [e.seq for e in chain] == list(range(len(chain))), [(e.seq, e.payload) for e in chain]
    assert all(b.prev == a.hash for a, b in zip(chain, chain[1:]))


# ── finding 18: a seal is requested by a signed operator, never by the token ─

def test_audit18_sealing_over_the_network_needs_a_signed_operator_request(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner = env.writer("own", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    space_id = _space_id(env.hub_dir)
    body = {"hub": HUB_ID, "space": space_id}
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.seal_request(None)
    assert exc.value.code == "unsigned"
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.seal_request(hubmod.sign_request("own", "seal.request", body, keys_dir=env.keys_dir, clock=env.clock))
    assert exc.value.code == "not_operator"
    ok = hub.seal_request(hubmod.sign_request(GRANTOR, "seal.request", body, keys_dir=env.keys_dir, clock=env.clock))
    assert ok["status"] == "sealed"
    again = hub.seal_request(hubmod.sign_request(GRANTOR, "seal.request", body, keys_dir=env.keys_dir, clock=env.clock))
    assert again["status"] == "unchanged"           # nothing new: no second seal event
    assert sum(1 for e in read_events(env.hub_dir) if e.type == "ledger.seal") == 1


# ════════════════════════════════════════════════════════════════════════════
# AUDIT FIXES, PART 2 (Codex audit 2026-10-18, findings 4, 8, 9, 10, 11, 12, 15, 17)
#
# Each test reproduces the audit's failure scenario and was seen failing
# before the fix.
# ════════════════════════════════════════════════════════════════════════════

def _hub_item(hub, item_id: str):
    """The item as the hub's authoritative state has it (admission + policy + lease authority)."""
    return hub._state()[1].items[item_id]


def _git_writer(env, actor: str) -> EventLog:
    """A writer whose events reach the hub by git, not HTTP (written straight into its checkout)."""
    return EventLog(env.hub_dir, actor, keys_dir=env.keys_dir, registry_path=env.registry, sign=True)


# ── finding 12: authorship is not authorization ─────────────────────────────

def test_audit12_a_signed_claim_of_work_addressed_to_another_is_refused_and_confers_nothing(env):
    hub = env.hub(eligibility=None)                      # the production assignee rule
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.append("item.claim", {"id": "t1"})
    assert exc.value.code == "not_authorized", exc.value
    # The same claim arriving by git is a valid signed proposal, not a transition.
    _git_writer(env, "wc").append("item.claim", {"id": "t1"})
    item = _hub_item(hub, "t1")
    assert item.status == "created" and item.owner is None, item.history
    assert wb.request_lease("t1")["status"] == "granted"


def test_audit12_a_pool_item_is_taken_and_completed_only_through_a_lease(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "p1", "title": "pool work", "role": "researcher"})
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.append("item.claim", {"id": "p1"})
    assert exc.value.code == "not_authorized", exc.value
    rogue = _git_writer(env, "wc")
    rogue.append("item.claim", {"id": "p1"})
    rogue.append("item.complete", {"id": "p1"})
    item = _hub_item(hub, "p1")
    assert item.status == "created" and item.owner is None, item.history


def test_audit12_only_the_approver_grants_and_cosign_is_checked_at_the_front_door(env):
    from ledger.policy import approval_payload_hash
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "for wa", "assignee": "wa"})
    wa.append("item.claim", {"id": "t1"})                 # addressed to wa: allowed
    payment = {"id": "pay", "title": "pay the invoice", "effects": ["payment"]}

    # A non-approver's grant, and a co-signed create without an approval.
    for actor, type, payload in (("wr1", "item.grant", {"id": "t1"}),
                                 ("wr2", "approval.grant", {"item": "pay",
                                                            "payload_hash": approval_payload_hash(payment)}),
                                 ("wr3", "item.create", payment)):
        with pytest.raises(hubmod.HubRefused) as exc:
            env.writer(actor, t).append(type, payload)
        assert exc.value.code == "not_authorized", (type, exc.value)
    # By git they are proposals with no effect.
    rogue = _git_writer(env, "wc")
    rogue.append("item.grant", {"id": "t1"})
    rogue.append("item.create", {**payment, "id": "pay-git"})
    _, state = hub._state()
    assert state.items["t1"].granted_by is None, state.items["t1"].history
    assert "pay-git" not in state.items

    # The approver's grant and an approved create go through.
    boss = env.writer(APPROVER, t)
    boss.append("item.grant", {"id": "t1"})
    grant, _ = boss.append("approval.grant", {"item": "pay", "payload_hash": approval_payload_hash(payment)})
    env.writer("wr4", t).append("item.create", {**payment, "approval_ref": grant.hash})
    _, state = hub._state()
    assert state.items["t1"].granted_by == APPROVER
    assert "pay" in state.items


# ── finding 9: every lease operation names its attempt ─────────────────────

def _lease_payload(answer: dict) -> dict:
    return answer["lease"]["payload"]


def test_audit9_a_lease_names_its_attempt_and_the_task_content_and_results_name_them_too(env):
    from ledger.fold import task_content_hash
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    create, _ = owner.append("item.create", {"id": "t1", "title": "task"})
    lease = _lease_payload(wa.request_lease("t1"))
    assert lease["attempt"] and lease["task_hash"] == task_content_hash(create.payload)
    done, _ = wa.append("item.complete", {"id": "t1"})
    assert done.payload["attempt"] == lease["attempt"] and done.payload["task_hash"] == lease["task_hash"]
    assert _hub_item(hub, "t1").status == "completed"


def test_audit9_a_completion_after_a_renewal_keeps_the_attempts_task_hash(env):
    """A renewal names the attempt but not the task content; the writer's
    completion must still carry the task hash pinned by that attempt's grant."""
    from ledger.fold import task_content_hash
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    create, _ = owner.append("item.create", {"id": "t1", "title": "task"})
    lease = _lease_payload(wa.request_lease("t1"))
    wa.renew_lease("t1")
    done, _ = wa.append("item.complete", {"id": "t1"})
    assert done.payload["attempt"] == lease["attempt"]
    assert done.payload["task_hash"] == task_content_hash(create.payload)
    item = _hub_item(hub, "t1")
    assert item.status == "completed", item.history


def test_audit9_a_result_after_expiry_goes_to_owner_resolution_not_completion(env, monkeypatch):
    """A's lease expires and the hub records the expiry; A's result arrives
    afterwards, stamped by a slow clock so it sorts BEFORE the expiry. It is
    recorded and marks the item for the owner; it completes nothing."""
    import time as _time
    import ledger.hlc as hlc
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    wa.request_lease("t1")
    env.clock.advance(901)
    expired = hub.expire_due()
    assert [e["payload"]["id"] for e in expired] == ["t1"]
    real = _time.time
    monkeypatch.setattr(hlc.time, "time", lambda: real() - 30)
    _, receipt = wa.append("item.complete", {"id": "t1", "result": "late"})
    monkeypatch.setattr(hlc.time, "time", real)
    assert receipt["status"] == "accepted"           # recorded ...
    item = _hub_item(hub, "t1")
    assert item.status != "completed", item.history   # ... but not a completion
    assert item.lease_unresolved, item.history
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code == "owner_resolution"


def test_audit9_a_stale_result_or_renewal_from_an_old_attempt_has_no_effect(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    first = _lease_payload(wa.request_lease("t1"))
    wa.append("item.release", {"id": "t1"})
    second = _lease_payload(wa.request_lease("t1"))
    assert second["attempt"] != first["attempt"]
    # A delayed result and a renewal from the first attempt.
    wa.append("item.complete", {"id": "t1", "attempt": first["attempt"], "task_hash": first["task_hash"]})
    assert _hub_item(hub, "t1").status == "claimed", _hub_item(hub, "t1").history
    old = hubmod.sign_request("wa", "lease.renew", {"hub": HUB_ID, "space": _space_id(env.hub_dir),
                                                     "item": "t1", "attempt": first["attempt"]},
                              keys_dir=env.keys_dir, clock=env.clock)
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.renew_lease(old)
    assert exc.value.code == "wrong_attempt"
    wa.append("item.complete", {"id": "t1"})          # the current attempt
    assert _hub_item(hub, "t1").status == "completed"


def test_audit9_a_renewal_after_expiry_is_refused_and_never_resurrects(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    wa.request_lease("t1")
    env.clock.advance(901)
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.renew_lease("t1")
    assert exc.value.code == "expired"
    # The fold agrees, whoever wrote it: a renewal after its attempt ended changes nothing.
    create = _raw_event(0, "1700000000000.0000.own", "own", "item.create", {"id": "t2"}, "GENESIS")
    lease = _raw_event(0, "1700000000001.0000.hubd", GRANTOR, "item.lease",
                       {"id": "t2", "holder": "wa", "attempt": "a1", "task_hash": "x",
                        "granted_ms": 100, "expires_ms": 200}, "GENESIS", hub=HUB_ID)
    late = _raw_event(1, "1700000000002.0000.hubd", GRANTOR, "item.lease.renew",
                      {"id": "t2", "holder": "wa", "attempt": "a1", "renewed_ms": 250, "expires_ms": 1250},
                      lease.hash, hub=HUB_ID)
    item = fold([create, lease, late], lease_grantors={HUB_ID: {GRANTOR}}).items["t2"]
    assert item.lease_expires_ms == 200, item.history


# ── finding 8: leases are resolved first, by (epoch, grantor sequence) ──────

def _grantor_lease(seq: int, hlc_ms: int, grantor: str, holder: str, attempt: str, granted: int,
                   expires: int, epoch: int = 0) -> Event:
    return _raw_event(seq, f"{hlc_ms:013d}.0000.{grantor}", grantor, "item.lease",
                      {"id": "t1", "holder": holder, "attempt": attempt, "task_hash": "x", "epoch": epoch,
                       "granted_ms": granted, "expires_ms": expires}, "x", hub=HUB_ID)


def test_audit8_a_completion_applied_first_cannot_hide_the_earlier_lease(env):
    """Seq 5 (granting wb) sorts before seq 3 (granting wa) because their HLCs
    disagree with sequence order, and wb's completion is folded before seq 3 is
    visited. The earlier lease still wins, and wb's completion is a conflict."""
    create = _raw_event(0, "1700000000000.0000.own", "own", "item.create", {"id": "t1"}, "GENESIS")
    later = _grantor_lease(5, 1_700_000_000_010, GRANTOR, "wb", "att-b", 1_000, 901_000)
    done = _raw_event(0, "1700000000011.0000.wb", "wb", "item.complete",
                      {"id": "t1", "attempt": "att-b", "task_hash": "x"}, "GENESIS")
    earlier = _grantor_lease(3, 1_700_000_000_020, GRANTOR, "wa", "att-a", 500, 900_500)
    for grantors in (None, {HUB_ID: {GRANTOR}}):
        item = fold([create, later, done, earlier], lease_grantors=grantors).items["t1"]
        assert item.lease_holder == "wa" and item.status == "claimed", item.history
        assert later.hash in item.lease_conflicts and done.hash in item.lease_conflicts, item.lease_conflicts


def test_audit8_disjoint_intervals_in_reverse_clock_order_are_not_an_overlap(env):
    """After a promotion the two grantors have different clocks. A lease
    [300, 400) from epoch 0 and a lease [100, 200) from epoch 1 do not overlap:
    each must begin before the other ends."""
    create = _raw_event(0, "1700000000000.0000.own", "own", "item.create", {"id": "t1"}, "GENESIS")
    old = _grantor_lease(0, 1_700_000_000_001, GRANTOR, "wa", "att-a", 300, 400, epoch=0)
    new = _grantor_lease(0, 1_700_000_000_002, "hubb", "wb", "att-b", 100, 200, epoch=1)
    item = fold([create, old, new], lease_grantors={HUB_ID: {GRANTOR: 0, "hubb": 1}}).items["t1"]
    assert not item.lease_conflicts, item.lease_conflicts
    assert item.lease_holder == "wb", item.history


# ── finding 4: promotion is an owner-signed ledger record that fences ──────



def _owned(env, name: str) -> Path:
    """A hub space whose opt-in (and cutover manifest) is an owner-signed ledger event."""
    space = env.tmp / name
    (space / ".datacore" / "events").mkdir(parents=True)
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900, owner=OWNER,
                  keys_dir=env.keys_dir, registry_path=env.registry)
    return space


def _writer_in(env, space: Path, actor: str, transport) -> "hubmod.HubWriter":
    d = env.tmp / f"{actor}-in-{space.name}"
    if not d.exists():
        (d / ".datacore" / "events").mkdir(parents=True)
        git_pull(space, d)
    return hubmod.HubWriter(d, actor, transport, keys_dir=env.keys_dir, registry_path=env.registry,
                            state_dir=env.tmp / "wstate" / f"{actor}-{space.name}", clock=env.clock,
                            policy=TEST_POLICY)


def _promote(env, space: Path, grantor: str, **kw):
    return hubmod.promote(space, grantor, by=OWNER, reason="test promotion", keys_dir=env.keys_dir,
                          registry_path=env.registry, **kw)


def test_audit4_promotion_is_an_owner_signed_record_that_fences_the_old_grantor(env):
    space = _owned(env, "p-space")
    hub_a = env.hub(space=space, state="pa")
    ta = hubmod.LocalTransport(hub_a)
    own, wa = _writer_in(env, space, OWNER, ta), _writer_in(env, space, "wa", ta)
    own.append("item.create", {"id": "t1", "title": "one"})
    own.append("item.create", {"id": "t2", "title": "two"})
    assert wa.request_lease("t1")["status"] == "granted"

    # No fence, no promotion: availability is traded for safety.
    with pytest.raises(hubmod.HubRefused) as exc:
        _promote(env, space, "hubb")
    assert exc.value.code == "unfenced"
    fence = hub_a.fence(reason="the owner is moving the hub")
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.request_lease("t2")
    assert exc.value.code == "fenced"

    _promote(env, space, "hubb")
    records = [e for e in read_events(space) if e.type == "hub.promote"]
    assert len(records) == 1 and records[0].actor == OWNER and records[0].sig, records
    p = records[0].payload
    assert p["epoch"] == 1 and p["grantor"] == "hubb" and p["previous"] == GRANTOR
    assert p["predecessor"]["frontier"][GRANTOR]["head"] == fence["hash"]

    # The new grantor waits until every lease of the old epoch has expired, plus the clock uncertainty.
    hub_b = env.hub("hubb", space, state="pb")
    wb = _writer_in(env, space, "wb", hubmod.LocalTransport(hub_b))
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t2")
    assert exc.value.code == "fencing_wait"
    env.clock.advance(900 + 3600)
    assert wb.request_lease("t2")["status"] == "granted"

    # Every new seal extends the predecessor's frontier.
    sealed = hub_b.seal()["seal"]["payload"]
    assert sealed["watermarks"][GRANTOR] >= fence["seq"]
    lost = env.tmp / "p-space-without-old-log"
    shutil.copytree(space, lost)
    (lost / ".datacore" / "events" / f"{GRANTOR}.jsonl").unlink()
    with pytest.raises(hubmod.HubRefused) as exc:
        env.hub("hubb", lost, state="pb2").seal()
    assert exc.value.code == "behind_predecessor"


def test_audit4_an_old_grantor_that_keeps_granting_after_promotion_confers_nothing(env):
    space = _owned(env, "q-space")
    hub_a = env.hub(space=space, state="qa")
    ta = hubmod.LocalTransport(hub_a)
    own = _writer_in(env, space, OWNER, ta)
    own.append("item.create", {"id": "t1", "title": "one"})
    owner_copy = env.tmp / "owner-copy"
    shutil.copytree(space, owner_copy)                 # the owner, cut off from hub A

    wb = _writer_in(env, space, "wb", ta)
    assert wb.request_lease("t1")["status"] == "granted"   # A, still running, grants

    _promote(env, owner_copy, "hubb", attest_stopped=True)
    hub_b = env.hub("hubb", owner_copy, state="qb")
    git_pull(space, owner_copy)                          # the partition heals
    assert _hub_item(hub_b, "t1").lease_holder is None   # A's grant after the fence frontier counts for nothing
    assert "fenced" in hub_b.refusals().get(GRANTOR, ""), hub_b.refusals()
    env.clock.advance(900 + 3600)
    hub_b.seal()
    report = wb.receipt_check(owner_copy, hub_actor="hubb")
    assert report["status"] == "failed", report

    git_pull(owner_copy, space)                          # A learns of the promotion
    from ledger.keys import ensure_keypair
    ensure_keypair("wa", keys_dir=env.keys_dir, registry_path=env.registry)
    with pytest.raises(hubmod.HubRefused) as exc:
        hub_a.request_lease(hubmod.sign_request("wa", "lease.request", {
            "hub": HUB_ID, "space": _space_id(space), "item": "t1"}, keys_dir=env.keys_dir, clock=env.clock))
    assert exc.value.code == "not_active_hub"


def test_audit4_a_config_edited_to_another_grantor_or_manifest_confers_nothing(env):
    space = _owned(env, "r-space")
    config = space / hubmod.CONFIG
    original = config.read_text()
    data = json.loads(original)
    data["grantor"], data["previous_grantors"] = "hubx", [GRANTOR]   # no owner-signed promotion
    config.write_text(json.dumps(data))
    from ledger.keys import ensure_keypair
    ensure_keypair("wa", keys_dir=env.keys_dir, registry_path=env.registry)
    hub_x = env.hub("hubx", space, state="rx")
    with pytest.raises(hubmod.HubRefused) as exc:
        hub_x.request_lease(hubmod.sign_request("wa", "lease.request", {
            "hub": HUB_ID, "space": _space_id(space), "item": "t1"}, keys_dir=env.keys_dir, clock=env.clock))
    assert exc.value.code == "not_active_hub"
    hub_x.close()
    # A manifest edited WITH a recomputed sha256 is still not the owner's.
    data = json.loads(original)
    data["cutover_logs"]["planted"] = {"seq": 0, "head": "0" * 64}
    data["cutover_sha256"] = hubmod._manifest_sha(data["cutover_logs"])
    config.write_text(json.dumps(data))
    with pytest.raises(hubmod.HubRefused) as exc:
        env.hub(space=space, state="ry")
    assert exc.value.code == "bad_manifest"


# ── finding 11: replay compares signed frontiers; refused events stay in the chain ─

def _signed_into(env, writer, type: str, payload: dict, *, chain: str | None = None,
                 hlc: str | None = None) -> Event:
    """A correctly signed, domain-bound event appended by hand to `writer`'s MAIN log
    file (for history the current writer library would route or refuse)."""
    from ledger.hub_admission import signing_domain
    from ledger.keys import sign
    path = writer.log.path
    lines = path.read_text().splitlines() if path.exists() else []
    tail = from_line(lines[-1]) if lines else None
    cfg = hubmod.load_config(writer.space_dir)
    body = body_dict(tail.seq + 1 if tail else 0, hlc or f"{_real_ms():013d}.0000.{writer.actor}", writer.actor, type,
                     payload, tail.hash if tail else "GENESIS", hub=cfg.hub_id,
                     domain=signing_domain(cfg.space_id, cfg.hub_id, chain or writer.log.log_name))
    e = Event(**body, hash=compute_hash(body), sig=sign(writer.actor, canonical_bytes(body), keys_dir=env.keys_dir))
    with open(path, "a", encoding="utf-8") as f:
        f.write(to_line(e) + "\n")
    return e


class _Lying(hubmod.LocalTransport):
    """An endpoint that overstates what the hub holds."""

    def status(self):
        st = dict(super().status())
        st["watermarks"] = {k: v + 5 for k, v in st.get("watermarks", {}).items()}
        st.pop("frontiers", None)
        return st


def test_audit11_replay_verifies_the_common_prefix_before_skipping_anything(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    wa = env.writer("wa", t)
    for i in range(2):
        wa.append("item.create", {"id": f"t{i}", "title": f"task {i}"})
    # wa's log forks: a second copy of it (another machine, restored from the
    # same backup) appends a different seq 2 and reaches the hub first.
    twin_dir = env.tmp / "wa-twin-space"
    shutil.copytree(wa.space_dir, twin_dir)
    twin = hubmod.HubWriter(twin_dir, "wa", t, keys_dir=env.keys_dir, registry_path=env.registry,
                            state_dir=env.tmp / "wstate" / "wa-twin", clock=env.clock)
    twin.append("item.create", {"id": "t2-twin", "title": "the twin's seq 2"})
    t.down = True
    wa.append("item.create", {"id": "t2", "title": "wa's own seq 2"})
    t.down = False
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.replay()                                      # used to skip seq 2 and clear the outage
    assert exc.value.code == "diverged", exc.value
    assert wa.outage() is not None                       # not reconciled: the outage stays
    # An endpoint that overstates the hub's frontier cannot make the writer skip.
    wb = env.writer("wb", t)
    wb.append("item.create", {"id": "b0", "title": "first"})
    t.down = True
    wb.append("item.create", {"id": "b1", "title": "buffered"})
    with pytest.raises(hubmod.HubRefused) as exc:
        hubmod.HubWriter(wb.space_dir, "wb", _Lying(hub), keys_dir=env.keys_dir, registry_path=env.registry,
                         state_dir=env.tmp / "wstate" / "wb", clock=env.clock).replay()
    assert exc.value.code == "bad_frontier", exc.value


def test_audit11_a_refused_event_already_in_the_writer_log_is_kept_as_a_rejected_proposal(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    git_pull(env.hub_dir, wa.space_dir)
    t.down = True
    bad = wa.log.append("item.lease", {"id": "t1", "holder": "wa", "granted_ms": 0, "expires_ms": 1})  # signed, local
    after, _ = wa.append("item.create", {"id": "t2", "title": "after it"})
    t.down = False
    replayed = wa.replay()
    assert [r["status"] for r in replayed] == ["rejected", "accepted"], replayed
    rejects = [e for e in read_events(env.hub_dir) if e.type == "ledger.reject"]
    assert len(rejects) == 1 and rejects[0].actor == GRANTOR
    _, state = hub._state()
    assert state.items["t1"].lease_holder is None and "t2" in state.items
    # The chain is continuous and settles: the seal covers it, and the receipt
    # for the event after the rejected one confirms. The rejected proposal is
    # never a confirmation of itself (Codex re-audit, finding 3), so a receipt
    # for every one of the writer's events fails on it.
    git = _published(env, hub, [owner, wa])
    report = wa.receipt_check(git, hub_actor=GRANTOR, required=[after.hash])
    assert report["status"] == "confirmed", report
    report = wa.receipt_check(git, hub_actor=GRANTOR)
    assert report["status"] == "failed" and any(bad.hash[:12] in p for p in report["problems"]), report


def test_audit11_a_telemetry_event_in_the_main_log_is_imported_into_the_main_log(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    wa = env.writer("wa", t)
    wa.append("item.create", {"id": "t1", "title": "task"})
    legacy = _signed_into(env, wa, "metric.attest", {"metric": "tokens", "value": 3})
    wa.append("item.create", {"id": "t2", "title": "after it"})
    wa.replay()
    hub_main = [from_line(ln) for ln in (env.hub_dir / ".datacore" / "events" / "wa.jsonl").read_text().splitlines()]
    assert legacy.hash in [e.hash for e in hub_main]
    assert (env.hub_dir / ".datacore" / "events" / "wa.jsonl").read_bytes() == wa.log.path.read_bytes()


# ── finding 10: a recovery barrier, not a fixed restart grace ──────────────

def test_audit10_a_holder_offline_longer_than_a_lease_period_is_not_double_run(env):
    """Two cases from the audit: the hub restarts and the holder stays away
    longer than one lease period; and a partition with no restart at all.
    Either way the item is not granted again until the holder reconciles."""
    for restart in (True, False):
        env.clock.advance(3600)
        hub = env.hub(state="s10")
        t = hubmod.LocalTransport(hub)
        owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
        item = f"t-{restart}"
        owner.append("item.create", {"id": item, "title": "task"})
        git_pull(env.hub_dir, wa.space_dir)
        assert wa.request_lease(item)["status"] == "granted"
        t.down = True
        wa.append("item.complete", {"id": item, "result": "done offline"})   # buffered
        if restart:
            hub.close()
            hub = env.hub(state="s10")                            # same persisted state
            t.hub = hub
        env.clock.advance(2 * 900)                                           # longer than one lease
        t.down = False
        with pytest.raises(hubmod.HubRefused) as exc:
            wb.request_lease(item)
        assert exc.value.code == "uncertain_attempt", (restart, exc.value)
        wa.replay()                                                          # the holder reconciles
        # Its completion was admitted after the lease ran out: the owner's to
        # decide, never a completion (Codex re-audit, finding 9; was "closed").
        with pytest.raises(hubmod.HubRefused) as exc:
            wb.request_lease(item)
        assert exc.value.code == "owner_resolution", (restart, exc.value)
        hub.close()


class _LosesFirstAnswer(hubmod.LocalTransport):
    """The request reaches the hub; its answer is lost on the way back, once."""

    def __init__(self, hub):
        super().__init__(hub)
        self.lost = 0

    def request_lease(self, req):
        answer = super().request_lease(req)
        if not self.lost:
            self.lost += 1
            raise hubmod.HubUnavailable("the answer was lost")
        return answer


def test_audit10_a_lost_answer_is_retried_and_the_hub_returns_the_same_signed_grant(env):
    hub = env.hub()
    owner = env.writer("own", hubmod.LocalTransport(hub))
    owner.append("item.create", {"id": "t1", "title": "task"})
    wa = env.writer("wa", _LosesFirstAnswer(hub))
    with pytest.raises(hubmod.HubUnavailable):
        wa.request_lease("t1")
    req = wa._pending("t1")
    assert req is not None                               # kept, to be retried as is
    answer = wa.request_lease("t1")                      # the same signed request, retried
    assert answer["status"] == "granted", answer
    assert [e.payload["holder"] for e in leases_in(env.hub_dir)] == ["wa"]   # one grant, not two
    assert len(wa.receipts()) == 1
    # A hub restarted on the same persisted state answers the retry the same way.
    hub.close()
    again = env.hub()
    assert again.request_lease(req)["lease"]["hash"] == answer["lease"]["hash"]


def test_audit10_a_restore_from_git_taken_before_a_grant_runs_the_recovery_barrier(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    hub_log = env.hub_dir / ".datacore" / "events" / f"{GRANTOR}.jsonl"
    before = hub_log.read_bytes() if hub_log.exists() else b""
    assert wa.request_lease("t1")["status"] == "granted"
    hub.close()
    hub_log.write_bytes(before)                          # the checkout restored from a copy before the grant
    hub = env.hub()                                      # same persisted request state
    t.hub = hub
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code == "recovering", exc.value
    wa.replay()                                          # wa presents the hub-signed grant it stored
    assert [e.payload["holder"] for e in leases_in(env.hub_dir)] == ["wa"]
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code == "taken", exc.value


# ── finding 17: a per-space protocol version every reader and writer must support ─

def test_audit17_a_reader_or_writer_without_the_spaces_protocol_refuses_the_space(env, monkeypatch):
    import ledger.hub_admission as adm
    hub = env.hub()
    wa = env.writer("wa", hubmod.LocalTransport(hub))
    wa.append("item.create", {"id": "t1", "title": "task"})
    assert hubmod.load_config(env.hub_dir).protocol == adm.PROTOCOL
    records = [e for e in read_events(env.hub_dir) if e.type == "hub.enable"]
    assert records and records[0].payload["protocol"] == adm.PROTOCOL
    # A host whose code supports only the base envelope: it must refuse the
    # space, not read it with a closed field list and mis-hash it.
    monkeypatch.setattr(adm, "SUPPORTED_PROTOCOLS", frozenset({1}))
    with pytest.raises(adm.ProtocolUnsupported):
        read_events(env.hub_dir)
    with pytest.raises(adm.ProtocolUnsupported):
        EventLog(env.hub_dir, "wz", keys_dir=env.keys_dir, registry_path=env.registry, sign=True)


def test_audit17_switching_a_space_on_is_gated_on_every_writer_declaring_support(env):
    space = env.tmp / "mixed-space"
    (space / ".datacore" / "events").mkdir(parents=True)
    for actor in ("own", "wa"):
        EventLog(space, actor, sign=False).append("item.create", {"id": f"{actor}-old", "title": "before"})
    with pytest.raises(hubmod.HubRefused) as exc:
        hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER, keys_dir=env.keys_dir,
                      registry_path=env.registry)
    assert exc.value.code == "not_ready" and "own" in exc.value.message and "wa" in exc.value.message
    assert hubmod.load_config(space) is None                 # nothing switched on
    hubmod.declare_ready(space, "own", keys_dir=env.keys_dir, registry_path=env.registry)
    with pytest.raises(hubmod.HubRefused) as exc:
        hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER, keys_dir=env.keys_dir,
                      registry_path=env.registry)
    assert exc.value.code == "not_ready" and "wa" in exc.value.message and "own" not in exc.value.message
    hubmod.declare_ready(space, "wa", keys_dir=env.keys_dir, registry_path=env.registry)
    cfg = hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, owner=OWNER, keys_dir=env.keys_dir,
                        registry_path=env.registry)
    assert cfg.protocol == 2


# ── finding 15: git stays the record and the fallback, under the own-log gate ─

def _git_env(monkeypatch):
    """A throwaway committer identity for the test repositories."""
    for k, v in (("GIT_AUTHOR_NAME", "t"), ("GIT_AUTHOR_EMAIL", "t@example.invalid"),
                 ("GIT_COMMITTER_NAME", "t"), ("GIT_COMMITTER_EMAIL", "t@example.invalid")):
        monkeypatch.setenv(k, v)


def _git(repo: Path, *args: str) -> str:
    import subprocess
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert r.returncode == 0, (args, r.stdout, r.stderr)
    return r.stdout


def _write_gate():
    """The repository's own ledger write gate (.datacore/lib/hooks/ledger_write_gate.py)."""
    import importlib.util
    path = Path(hubmod.__file__).resolve().parents[1] / "hooks" / "ledger_write_gate.py"
    spec = importlib.util.spec_from_file_location("ledger_write_gate_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_repo(repo: Path) -> str:
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    return _git(repo, "rev-parse", "HEAD").strip()


def test_audit15_a_writer_publishes_its_own_log_through_git_while_the_hub_is_down(env, monkeypatch):
    ranges = _write_gate().ranges
    _git_env(monkeypatch)
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    wa = env.writer("wa", t)
    base = _git_repo(wa.space_dir)
    t.down = True
    wa.append("item.create", {"id": "t1", "title": "addressed work, hub down"})   # buffered locally
    # Someone else's change sits in the same checkout; publishing must not take it.
    (wa.space_dir / ".datacore" / "events" / "wb.jsonl").write_text("not wa's\n")
    commit = wa.publish("ledger: wa while the hub is down")
    assert _git(wa.space_dir, "show", "--name-only", "--format=", commit).split() == [".datacore/events/wa.jsonl"]
    # The repository's own write gate (pre-commit / pre-push) accepts hub-space lines.
    assert ranges(wa.space_dir, [f"{base}..{commit}"]) == []
    assert "wb.jsonl" in _git(wa.space_dir, "status", "--porcelain")   # left exactly as found


def test_audit15_the_hub_publishes_only_its_own_log(env, monkeypatch):
    ranges = _write_gate().ranges
    _git_env(monkeypatch)
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    base = _git_repo(env.hub_dir)
    owner.append("item.create", {"id": "t1", "title": "task"})       # lands in the hub's checkout too
    wa.request_lease("t1")
    hub.seal()
    commit = hub.publish("ledger: hub grants and seal")
    assert _git(env.hub_dir, "show", "--name-only", "--format=", commit).split() == [
        f".datacore/events/{GRANTOR}.jsonl"]
    assert ranges(env.hub_dir, [f"{base}..{commit}"]) == []
    assert "own.jsonl" in _git(env.hub_dir, "status", "--porcelain")   # another writer's log: theirs to publish


# ── audit fixes, part 3: the Codex re-audit of 2026-10-18 ──────────────────
# Report: 2-datacore/1-tracks/engineering/reports/2026-10-18-ledger-hub-codex-reaudit-report.md.
# Each test reproduces the scenario Codex ran.

def _last(writer) -> Event:
    return from_line(writer.log.path.read_text().splitlines()[-1])


def _tail(writer) -> dict:
    e = _last(writer)
    return {writer.log.log_name: {"seq": e.seq, "head": e.hash}}


# A (finding 3): a receipt confirms an operation only when its transition took effect.

def test_reaudit3_a_policy_rejected_claim_is_never_a_confirmed_receipt(env):
    """Codex: a claim the policy refused returned `confirmed, ok: true` while
    the item stayed unclaimed. Here over HTTP (kept as a rejected proposal)
    and by git (folded to a no-op)."""
    hub = env.hub(eligibility=None)
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    owner.append("item.create", {"id": "t2", "title": "for wb too", "assignee": "wb"})
    git_pull(env.hub_dir, wa.space_dir)
    with pytest.raises(hubmod.HubRefused) as exc:
        wa.append("item.claim", {"id": "t1"})
    assert exc.value.code == "not_authorized"
    over_http = _last(wa)
    t.down = True
    by_git, _ = wa.append("item.claim", {"id": "t2"})          # buffered, then published by git
    git_pull(wa.space_dir, env.hub_dir)
    t.down = False
    git = _published(env, hub, [owner, wa])
    _, state = hub._state()
    assert state.items["t1"].owner is None and state.items["t2"].owner is None
    for claim in (over_http, by_git):
        report = wa.receipt_check(git, hub_actor=GRANTOR, required=[claim.hash])
        assert report["status"] == "failed" and report["ok"] is False, report
        assert any(claim.hash[:12] in p for p in report["problems"]), report
    report = wa.receipt_check(git, hub_actor=GRANTOR)            # every event of mine
    assert report["status"] == "failed" and report["ok"] is False, report


def test_reaudit3_a_claim_that_changed_nothing_is_not_a_confirmed_receipt(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "anyone's"})
    git_pull(env.hub_dir, wa.space_dir)
    wa.append("item.claim", {"id": "t1"})
    git_pull(env.hub_dir, wb.space_dir)
    late, _ = wb.append("item.claim", {"id": "t1"})               # authorized, but already claimed
    git = _published(env, hub, [owner, wa, wb])
    assert wa.receipt_check(git, hub_actor=GRANTOR)["status"] == "confirmed"
    report = wb.receipt_check(git, hub_actor=GRANTOR, required=[late.hash])
    assert report["status"] == "failed" and report["ok"] is False, report


# B (finding 2): one authoritative state for every reader of a hub space.

def test_reaudit2_every_reader_of_a_hub_space_gets_the_one_authoritative_state(env):
    hub = env.hub(eligibility=None)
    t = hubmod.LocalTransport(hub)
    owner = env.writer("own", t)
    owner.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    owner.append("item.create", {"id": "t2", "title": "anyone's"})
    _git_writer(env, "wc").append("item.claim", {"id": "t1"})                  # the policy refuses it
    _git_writer(env, "wd").append("item.lease", {"id": "t2", "holder": "wd", "granted_ms": env.clock(),
                                                 "expires_ms": env.clock() + 900_000})   # not a grantor
    hub.seal()
    kw = dict(registry_path=env.registry, policy=TEST_POLICY, now_ms=env.clock())
    state = hubmod.authoritative_state(env.hub_dir, **kw)
    assert state.items["t1"].owner is None and state.items["t2"].lease_holder is None
    assert state.state_root() == hub._state()[1].state_root()
    settled = hubmod.authoritative_state(env.hub_dir, settled=True, **kw)
    assert settled.items["t1"].owner is None and settled.items["t2"].lease_holder is None
    # A plain fold of the same files is exactly the bypass.
    assert fold(read_events(env.hub_dir)).items["t1"].owner == "wc"
    # A space without a hub keeps the old path.
    plain = env.tmp / "plain-space"
    EventLog(plain, "own", sign=False).append("item.create", {"id": "p1", "title": "plain"})
    EventLog(plain, "wc", sign=False).append("item.claim", {"id": "p1"})
    assert hubmod.authoritative_state(plain).state_root() == fold(read_events(plain)).state_root()


def test_reaudit2_no_production_reader_folds_a_space_without_admission():
    """Every production `fold(read_events(space))` could be a hub space read
    around admission and policy; they go through `ledger.hub.authoritative_state`."""
    import re
    lib = Path(hubmod.__file__).resolve().parents[1]
    pattern = re.compile(r"\bfold\(\s*read_events\(")
    offenders = [f"{p.relative_to(lib)}:{n}" for p in sorted(lib.rglob("*.py")) if "tests" not in p.parts
                 for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if pattern.search(line)]
    # ... and the two-line form: `events = read_events(space)` then `fold(events)`.
    two = re.compile(r"(\w+)\s*=\s*read_events\([^\n]*\n(?:[^\n]*\n){0,2}?[^\n]*\bfold\(\s*\1\s*\)")
    offenders += [f"{p.relative_to(lib)}: {m.group(0).splitlines()[-1].strip()}" for p in sorted(lib.rglob("*.py"))
                  if "tests" not in p.parts
                  for m in two.finditer(p.read_text(encoding="utf-8", errors="replace"))]
    assert offenders == [], offenders


# C (new 1): a rejected proposal is a no-op on every replay, before policy.

def test_reaudit_new1_a_rejected_backdated_claim_stays_a_no_op_on_every_replay(env):
    """Codex: a claim refused at the front door, stamped before the item was
    reassigned, became valid on replay (it ended owner=wa, assignee=wb)."""
    hub = env.hub(eligibility=None)
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    import time as _time
    create, _ = owner.append("item.create", {"id": "t1", "title": "first for wa", "assignee": "wa"})
    _time.sleep(0.05)
    update, _ = owner.append("item.update", {"id": "t1", "assignee": "wb"})
    git_pull(env.hub_dir, wa.space_dir)
    physical = (int(create.hlc.split(".")[0]) + int(update.hlc.split(".")[0])) // 2   # between the two
    claim = _signed_into(env, wa, "item.claim", {"id": "t1"}, hlc=f"{physical:013d}.0000.wa")
    replayed = wa.replay()
    assert [r["status"] for r in replayed] == ["rejected"], replayed
    assert [e.payload["event"]["hash"] for e in read_events(env.hub_dir) if e.type == "ledger.reject"] == [claim.hash]
    item = _hub_item(hub, "t1")
    assert item.owner is None and item.status == "created" and item.payload["assignee"] == "wb", item.history
    sealed = hub.seal()["seal"]["payload"]
    assert sealed["state_root"] == hubmod.authoritative_state(
        env.hub_dir, registry_path=env.registry, policy=TEST_POLICY, now_ms=env.clock()).state_root()
    assert wb.request_lease("t1")["status"] == "granted"


# D (new 2): a crash between storing a proposal and its rejection does not strand the chain.

@pytest.mark.parametrize("crash", ["after_proposal", "after_reject"])
def test_reaudit_new2_a_crash_between_a_proposal_and_its_rejection_does_not_strand_the_chain(env, monkeypatch,
                                                                                            crash):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa = env.writer("own", t), env.writer("wa", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    git_pull(env.hub_dir, wa.space_dir)
    wa.log.append("item.lease", {"id": "t1", "holder": "wa", "granted_ms": 0, "expires_ms": 1})  # signed, local

    class Crash(Exception):
        pass

    real_append = hub.log.append

    def dies(type, payload, **kw):
        if type == "ledger.reject":
            if crash == "after_reject":
                real_append(type, payload, **kw)       # durable, but the answer never leaves
            raise Crash("the hub process died here")
        return real_append(type, payload, **kw)

    monkeypatch.setattr(hub.log, "append", dies)
    with pytest.raises(Crash):
        wa.replay()
    hub.close()
    hub = env.hub()                                    # restarted on the same persisted state
    t.hub = hub
    wa.replay()
    _, receipt = wa.append("item.create", {"id": "t2", "title": "after the crash"})
    assert receipt and receipt["status"] == "accepted", receipt
    rejects = [e for e in read_events(env.hub_dir) if e.type == "ledger.reject"]
    assert len(rejects) == 1 and rejects[0].payload["event"]["seq"] == 0, rejects
    _, state = hub._state()
    assert state.items["t1"].lease_holder is None and "t2" in state.items
    assert "wa" not in hub.refusals(), hub.refusals()


# E (new 3): recovery-required is persisted, cleared only by the owner's transition.

def test_reaudit_new3_a_lost_request_store_stays_recovering_after_a_refused_request_and_a_restart(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    owner.append("item.create", {"id": "t2", "title": "task"})
    assert wa.request_lease("t1")["status"] == "granted"
    store = hub._requests_path
    hub.close()
    store.unlink()                                       # the request store is lost
    hub = env.hub()
    t.hub = hub
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t2")                           # refused, and the refusal is stored
    assert exc.value.code == "recovering"
    hub.close()
    hub = env.hub()                                      # the store file exists again
    t.hub = hub
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t2")
    assert exc.value.code == "recovering", exc.value
    # Only the owner's verified recovery transition clears it, and that survives a restart.
    hub.clear_recovery(hubmod.sign_request(OWNER, "recovery.clear", {"hub": HUB_ID, "space": _space_id(env.hub_dir)},
                                           keys_dir=env.keys_dir, clock=env.clock))
    hub.close()
    hub = env.hub()
    t.hub = hub
    assert wb.request_lease("t2")["status"] == "granted"


# F (new 4): reconciling needs every required log and an explicit outcome per uncertain attempt.

def test_reaudit_new4_an_empty_reconcile_does_not_unlock_an_uncertain_attempt(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    owner.append("item.create", {"id": "t2", "title": "task"})
    git_pull(env.hub_dir, wa.space_dir)
    first = _lease_payload(wa.request_lease("t1"))["attempt"]
    second = _lease_payload(wa.request_lease("t2"))["attempt"]
    wa.append("item.update", {"id": "t1", "note": "working"})       # the hub holds a log of wa's
    env.clock.advance(901)
    hub.expire_due()

    def reconcile(frontier):
        return hub.reconcile(hubmod.sign_request("wa", "reconcile", {
            "hub": HUB_ID, "space": _space_id(env.hub_dir), "frontier": frontier, "leases": []},
            keys_dir=env.keys_dir, clock=env.clock))

    with pytest.raises(hubmod.HubRefused) as exc:
        reconcile({})                                    # Codex's case: an empty frontier, no leases
    assert exc.value.code == "behind", exc.value
    answer = reconcile(_tail(wa))
    assert sorted(answer["unresolved"]) == sorted([first, second]), answer
    for item in ("t1", "t2"):
        with pytest.raises(hubmod.HubRefused) as exc:
            wb.request_lease(item)
        assert exc.value.code == "uncertain_attempt", (item, exc.value)
    # The holder resolves one explicitly: it releases that attempt.
    wa.append("item.release", {"id": "t1"})
    assert wb.request_lease("t1")["status"] == "granted"
    # A holder that never returns: only the owner's signed resolution frees the item.
    hubmod.resolve_attempt(env.hub_dir, "t2", second, by=OWNER, reason="wa is gone; nothing ran",
                           keys_dir=env.keys_dir, registry_path=env.registry)
    assert wb.request_lease("t2")["status"] == "granted"


# G (finding 9): a result admitted after the lease ran out is not a completion.

def test_reaudit9_a_result_admitted_after_the_lease_ran_out_is_not_a_completion(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "t1", "title": "task"})
    owner.append("item.create", {"id": "t2", "title": "task"})
    wa.request_lease("t1")
    env.clock.advance(901)                               # no sweep ran: no expiry event exists
    _, receipt = wa.append("item.complete", {"id": "t1", "result": "late"})
    assert receipt["status"] == "accepted"
    item = _hub_item(hub, "t1")
    assert item.status != "completed" and item.lease_unresolved, item.history
    assert [e.payload["id"] for e in read_events(env.hub_dir) if e.type == "item.lease.expire"] == ["t1"]
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code == "owner_resolution"
    # A release names the task content its attempt pinned, like a completion.
    lease = _lease_payload(wa.request_lease("t2"))
    wa.append("item.release", {"id": "t2", "attempt": lease["attempt"], "task_hash": "0" * 64})
    item = _hub_item(hub, "t2")
    assert item.status == "claimed" and item.lease_holder == "wa", item.history


# H (finding 12): a lease obeys the same execution policy as a claim.

def test_reaudit12_a_lease_obeys_the_same_execution_policy_as_a_claim(env):
    from ledger.hub_policy import PolicyInputs
    policy = PolicyInputs(approver=APPROVER, cosign_effects=frozenset({"payment"}),
                          known_effects=frozenset({"payment", "deploy"}), principal_of={"wa": "wa"},
                          limits={"wa": {"never_effects": ["deploy"]}})
    hub = env.hub(policy=policy)                         # the permissive pool hook: policy still applies
    t = hubmod.LocalTransport(hub)
    owner, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    owner.append("item.create", {"id": "d1", "title": "deploy it", "effects": ["deploy"]})
    owner.append("item.create", {"id": "a1", "title": "for wb", "assignee": "wb"})
    for item in ("d1", "a1"):
        with pytest.raises(hubmod.HubRefused) as exc:
            wa.request_lease(item)
        assert exc.value.code == "not_authorized", (item, exc.value)
    assert wb.request_lease("a1")["status"] == "granted"
    # The fold agrees: a grantor's lease the policy would refuse confers nothing.
    create = _raw_event(0, "1700000000000.0000.own", "own", "item.create",
                        {"id": "d2", "title": "deploy", "effects": ["deploy"]}, "GENESIS")
    lease = _raw_event(0, "1700000000001.0000.hubd", GRANTOR, "item.lease",
                       {"id": "d2", "holder": "wa", "attempt": "a1", "task_hash": "x",
                        "granted_ms": 100, "expires_ms": 200}, "GENESIS", hub=HUB_ID)
    item = fold([create, lease], lease_grantors={HUB_ID: {GRANTOR}}, policy=policy).items["d2"]
    assert item.lease_holder is None, item.history


# I (finding 5): the first confirmation refuses an expired grant and another epoch.

def test_reaudit5_a_first_confirmation_refuses_an_expired_grant_or_another_epoch(env):
    hub = env.hub()
    owner = env.writer("own", hubmod.LocalTransport(hub))
    owner.append("item.create", {"id": "t1", "title": "task"})

    def forged(changes):
        def answer(req, real):              # genuinely signed by the grantor, in its own log
            p = {"id": "t1", "holder": "wa", "attempt": hubmod.attempt_id(req), "task_hash": "x", "epoch": 0,
                 "granted_ms": env.clock(), "expires_ms": env.clock() + 900_000,
                 "request": hubmod._request_ref(req), **changes}
            return {"status": "granted", "lease": json.loads(to_line(hub.log.append("item.lease", p))),
                    "log": GRANTOR}
        return answer

    for changes in ({"epoch": 3},
                    {"granted_ms": env.clock() - 2_000_000, "expires_ms": env.clock() - 1_000_000}):
        wa = env.writer("wa", _Forging(hub, forged(changes)))
        with pytest.raises(hubmod.HubRefused) as exc:
            wa.request_lease("t1")
        assert exc.value.code == "bad_lease", (changes, exc.value)
        assert wa.receipts() == []


# J (finding 4): a successor's seal covers everything its predecessor's last seal covered.

def test_reaudit4_a_successor_seal_covers_every_prefix_the_predecessors_last_seal_covered(env):
    from ledger.seal import build_seal_payload
    space = _owned(env, "j-space")
    hub_a = env.hub(space=space, state="ja")
    ta = hubmod.LocalTransport(hub_a)
    own, wa = _writer_in(env, space, OWNER, ta), _writer_in(env, space, "wa", ta)
    own.append("item.create", {"id": "t1", "title": "one"})
    wa.append("item.create", {"id": "w1", "title": "wa's settled work"})
    assert hub_a.seal()["seal"]["payload"]["watermarks"]["wa"] == 0
    hub_a.fence(reason="moving the hub")
    _promote(env, space, "hubb")
    lost = env.tmp / "j-space-without-wa"
    shutil.copytree(space, lost)
    (lost / ".datacore" / "events" / "wa.jsonl").unlink()     # a successor copy that lost wa's prefix
    hub_b = env.hub("hubb", lost, state="jb")
    with pytest.raises(hubmod.HubRefused) as exc:
        hub_b.seal()
    assert exc.value.code == "behind_predecessor", exc.value
    # A dishonest successor seals anyway; another writer's receipt must not confirm on it.
    wb = _writer_in(env, lost, "wb", hubmod.LocalTransport(hub_b))
    wb.append("item.create", {"id": "b1", "title": "wb's work"})
    events, _ = hub_b._state()
    EventLog(lost, "hubb", keys_dir=env.keys_dir, registry_path=env.registry, sign=True).append(
        "ledger.seal", build_seal_payload(events, fold_kwargs=hub_b._fold_kwargs(hubmod.load_config(lost))))
    report = wb.receipt_check(lost, hub_actor="hubb")
    assert report["status"] == "failed" and any("wa" in p for p in report["problems"]), report


# ── round 4: a receipt confirms only an effect that stands and is final ─────
# (Codex round-3 verdict: void_receipt, proposal_late, imported_late_result,
# sealed_ownership.) A "confirmed" receipt never later becomes "failed".

def _with_principals(env, monkeypatch):
    """A principal registry in which the owner may void (voids read it)."""
    import actor_identity
    registry = env.tmp / "principals.yaml"
    registry.write_text("principals:\n  owner: {kind: human}\n  own: {kind: human}\n"
                        "  wa: {kind: agent}\n  wb: {kind: agent}\n")
    monkeypatch.setattr(actor_identity, "PRINCIPALS", registry)


def test_round4_a_voided_completion_is_never_a_confirmed_receipt(env, monkeypatch):
    _with_principals(env, monkeypatch)
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    wa.append("item.claim", {"id": "t"})
    done, _ = wa.append("item.complete", {"id": "t"})
    # Round 6 (owner decision C5): in a hub space only the owner the owner's
    # records name reverses; a human principal of the registry no longer does.
    owner = env.writer(OWNER, t)
    owner.append("ledger.void", {"log": "wa", "seq": done.seq, "hash": done.hash, "reason": "completion withdrawn"})
    published = _published(env, hub, [wa, own, owner])
    assert hub._state()[1].items["t"].status == "claimed"      # the void took the effect away
    report = wa.receipt_check(published, required=[done.hash])
    assert report["status"] == "failed" and not report["ok"], report


def test_round4_the_rejected_proposal_flag_never_skips_the_late_result_rule(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    grant = wa.request_lease("t")["lease"]["payload"]
    env.clock.advance(901)
    done = _signed_into(env, wa, "item.complete", {"id": "t", "attempt": grant["attempt"],
                                                   "task_hash": grant["task_hash"]})
    hub.append(wa._submission(done, rejected=True))
    item = _hub_item(hub, "t")
    assert item.status != "completed", item.history
    assert done.hash in item.lease_unresolved, item.history
    report = wa.receipt_check(_published(env, hub, [wa, own]), required=[done.hash])
    assert report["status"] != "confirmed" and not report["ok"], report


def test_round4_a_late_result_imported_by_git_is_not_a_completion(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    grant = wa.request_lease("t")["lease"]["payload"]
    env.clock.advance(901)
    done = _signed_into(env, wa, "item.complete", {"id": "t", "attempt": grant["attempt"],
                                                   "task_hash": grant["task_hash"]})
    git_pull(wa.space_dir, env.hub_dir)                  # reaches the hub by git, not by append
    published = _published(env, hub, [own, wa])
    item = _hub_item(hub, "t")
    assert item.status != "completed", item.history
    assert done.hash in item.lease_unresolved, item.history
    report = wa.receipt_check(published, required=[done.hash])
    assert report["status"] != "confirmed" and not report["ok"], report


def test_round4_an_imported_result_that_was_in_time_still_completes(env):
    """The boundary of the rule above: a result the hub first sees while its
    lease is still live completes, whichever path brought it."""
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    grant = wa.request_lease("t")["lease"]["payload"]
    env.clock.advance(60)
    done = _signed_into(env, wa, "item.complete", {"id": "t", "attempt": grant["attempt"],
                                                   "task_hash": grant["task_hash"]})
    git_pull(wa.space_dir, env.hub_dir)
    hub.seal()                                           # the hub judges it while the lease is live
    env.clock.advance(901)
    published = _published(env, hub, [own, wa])
    assert _hub_item(hub, "t").status == "completed"
    assert wa.receipt_check(published, required=[done.hash])["status"] == "confirmed"


def test_round4_a_confirmed_claim_is_final_when_an_earlier_stamped_claim_arrives_later(env):
    import time as _time
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    own, wa, wb = [env.writer(a, t) for a in ("own", "wa", "wb")]
    base = int(_time.time() * 1000)
    create = _signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{base:013d}.0000.own")
    hub.append(own._submission(create))
    a = _signed_into(env, wa, "item.claim", {"id": "t"}, hlc=f"{base + 200:013d}.0000.wa")
    hub.append(wa._submission(a))
    before = wa.receipt_check(_published(env, hub, [wa]), required=[a.hash])
    assert before["status"] == "confirmed", before
    b = _signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{base + 100:013d}.0000.wb")
    hub.append(wb._submission(b))
    after = wa.receipt_check(_published(env, hub, [wa, wb]), required=[a.hash])
    assert after["status"] == "confirmed", after
    assert _hub_item(hub, "t").owner == "wa"
    assert wb.receipt_check(_published(env, hub, [wa, wb]), required=[b.hash])["status"] == "failed"


def test_round4_a_sealed_claim_is_final_when_the_earlier_claim_arrives_by_git(env):
    import time as _time
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    own, wa, wb = [env.writer(a, t) for a in ("own", "wa", "wb")]
    base = int(_time.time() * 1000)
    hub.append(own._submission(_signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{base:013d}.0000.own")))
    a = _signed_into(env, wa, "item.claim", {"id": "t"}, hlc=f"{base + 200:013d}.0000.wa")
    hub.append(wa._submission(a))
    assert wa.receipt_check(_published(env, hub, [wa]), required=[a.hash])["status"] == "confirmed"
    _signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{base + 100:013d}.0000.wb")
    git_pull(wb.space_dir, env.hub_dir)
    after = wa.receipt_check(_published(env, hub, [wa, wb]), required=[a.hash])
    assert after["status"] == "confirmed", after
    assert _hub_item(hub, "t").owner == "wa"


# ════════════════════════════════════════════════════════════════════════════
# ROUND 4: ownership paths (Codex round-3 verdict 2026-10-19, `reassign` and
# `owner_override`). Written before the fix and seen failing.
# ════════════════════════════════════════════════════════════════════════════

def _arbitrated_policy():
    from ledger.hub_policy import PolicyInputs
    return PolicyInputs(approver=APPROVER, cosign_effects=frozenset(), arbitration=("owner", "wb", "wa"))


def test_r4_an_agent_may_not_reassign_someone_elses_task_to_itself(env):
    """`reassign`: wa's claim on wb's task is refused, so wa must not be able to
    rewrite the assignee to itself and then claim. Changing `assignee` is an
    ownership change and passes the same authority as granting the task."""
    hub = env.hub(policy=_arbitrated_policy())
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    with pytest.raises(hubmod.HubRefused):
        wa.append("item.claim", {"id": "t1"})
    with pytest.raises(hubmod.HubRefused):
        wa.append("item.update", {"id": "t1", "assignee": "wa"})
    with pytest.raises(hubmod.HubRefused):
        wa.append("item.claim", {"id": "t1"})
    item = _hub_item(hub, "t1")
    assert item.owner is None and item.payload["assignee"] == "wb", item.history


def test_r4_a_reassignment_that_arrives_by_git_is_a_no_op_on_replay(env):
    """The same edit signed straight into wa's log and published by git: the fold
    applies the same rule, so no copy of the log sees wa as the assignee."""
    hub = env.hub(policy=_arbitrated_policy())
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    git_pull(env.hub_dir, wa.space_dir)
    _signed_into(env, wa, "item.update", {"id": "t1", "assignee": "wa"})
    git_pull(wa.space_dir, env.hub_dir)
    item = _hub_item(hub, "t1")
    assert item.payload["assignee"] == "wb", item.history
    assert any("not authorized" in h for h in item.history), item.history


def test_r4_the_creator_and_the_approver_may_still_reassign(env):
    """Boundary: the rule does not freeze assignment. The task's creator and the
    approver reassign; the new assignee then claims normally."""
    hub = env.hub(policy=_arbitrated_policy())
    t = hubmod.LocalTransport(hub)
    own, boss, wa = env.writer("own", t), env.writer(APPROVER, t), env.writer("wa", t)
    own.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    own.append("item.create", {"id": "t2", "title": "for wb too", "assignee": "wb"})
    own.append("item.update", {"id": "t1", "assignee": "wa"})
    boss.append("item.update", {"id": "t2", "assignee": "wa"})
    wa.append("item.claim", {"id": "t1"})
    assert _hub_item(hub, "t1").owner == "wa"
    assert _hub_item(hub, "t2").payload["assignee"] == "wa"
    # ... and the current assignee may hand its own task on.
    wb = env.writer("wb", t)
    own.append("item.create", {"id": "t3", "title": "wb's", "assignee": "wb"})
    wb.append("item.update", {"id": "t3", "assignee": "wa"})
    assert _hub_item(hub, "t3").payload["assignee"] == "wa"


def test_r4_owner_set_on_an_unowned_task_respects_its_assignment(env):
    """`owner_override`: on an unowned task assigned to wb, wa's owner.set {owner: wa}
    was accepted (arbitration looked only at an already-populated owner)."""
    hub = env.hub(policy=_arbitrated_policy())
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    with pytest.raises(hubmod.HubRefused):
        wa.append("owner.set", {"id": "t1", "owner": "wa"})
    git_pull(env.hub_dir, wa.space_dir)
    _signed_into(env, wa, "owner.set", {"id": "t1", "owner": "wa"})     # and by git
    git_pull(wa.space_dir, env.hub_dir)
    item = _hub_item(hub, "t1")
    assert item.owner is None and item.payload["assignee"] == "wb", item.history


def test_r4_owner_set_by_the_approver_or_to_the_assignee_is_still_allowed(env):
    """Boundary: owner.set stays the approver's override, and setting the owner to
    a principal the task is addressed to is a claim's equivalent."""
    hub = env.hub(policy=_arbitrated_policy())
    t = hubmod.LocalTransport(hub)
    own, boss, wb = env.writer("own", t), env.writer(APPROVER, t), env.writer("wb", t)
    own.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb"})
    own.append("item.create", {"id": "t2", "title": "for wb", "assignee": "wb"})
    wb.append("owner.set", {"id": "t1", "owner": "wb"})
    boss.append("owner.set", {"id": "t2", "owner": "wa"})
    assert _hub_item(hub, "t1").owner == "wb"
    assert _hub_item(hub, "t2").owner == "wa"


def test_r4_create_may_not_hand_ownership_around_the_owner_rules(env):
    """`item.create` may carry `owner` (fold `_handle_create`), and the hub did not
    check it. "Assigned to wb, owned by wa" put wa in charge of a task addressed to
    wb -- the owner.set that would do the same is refused -- and as owner wa could
    then reassign the task to itself and claim it. Creating with owner=wb made wb
    owner without claiming. A creation's owner is the creator itself, eligible
    under the assignment, unless the approver creates it."""
    hub = env.hub(policy=_arbitrated_policy())
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    with pytest.raises(hubmod.HubRefused):
        own.append("item.create", {"id": "t1", "title": "for wb", "assignee": "wb", "owner": "wa"})
    with pytest.raises(hubmod.HubRefused):
        wa.append("item.create", {"id": "t2", "title": "for wb", "assignee": "wb", "owner": "wa"})
    with pytest.raises(hubmod.HubRefused):
        wa.append("item.create", {"id": "t3", "title": "wb's now", "owner": "wb"})
    # ... and by git: on replay the creation is a recorded no-op, so wa never
    # owns t1 and cannot use ownership to reassign it to itself and claim.
    git_pull(env.hub_dir, wa.space_dir)
    _signed_into(env, wa, "item.create", {"id": "t1", "title": "for wb", "assignee": "wb", "owner": "wa"})
    git_pull(wa.space_dir, env.hub_dir)
    assert "t1" not in hub._state()[1].items or _hub_item(hub, "t1").owner is None
    for type, payload in (("item.update", {"id": "t1", "assignee": "wa"}), ("item.claim", {"id": "t1"})):
        try:
            wa.append(type, payload)
        except hubmod.HubRefused:
            pass
    items = hub._state()[1].items
    assert "t1" not in items or items["t1"].owner is None, items["t1"].history
    assert "t2" not in items and "t3" not in items


def test_r4_create_with_an_owner_still_works_for_the_creator_and_the_approver(env):
    """Boundary: a creator may own what it may execute, the approver may create a
    task owned by anyone, and re-creating an existing id changes nothing."""
    hub = env.hub(policy=_arbitrated_policy())
    t = hubmod.LocalTransport(hub)
    boss, wa = env.writer(APPROVER, t), env.writer("wa", t)
    wa.append("item.create", {"id": "t1", "title": "mine", "owner": "wa"})
    wa.append("item.create", {"id": "t2", "title": "mine", "assignee": "wa", "owner": "wa"})
    boss.append("item.create", {"id": "t3", "title": "for wb", "assignee": "wb", "owner": "wa"})
    assert _hub_item(hub, "t1").owner == "wa"
    assert _hub_item(hub, "t2").owner == "wa"
    assert _hub_item(hub, "t3").owner == "wa"
    own = env.writer("own", t)
    own.append("item.create", {"id": "t4", "title": "for wb", "assignee": "wb"})
    try:
        wa.append("item.create", {"id": "t4", "title": "taken", "owner": "wa"})
    except hubmod.HubRefused:
        pass
    item = _hub_item(hub, "t4")
    assert item.owner is None and item.payload["assignee"] == "wb", item.history


# ── round 4: decision readers go through admission (`spend_reader`,
#    `raw_gate_readers`, `autofix_reader`) ──────────────────────────────────

def _default_keys(env, monkeypatch):
    """Production readers take no key arguments: point the defaults at the env."""
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_KEYS_DIR", env.keys_dir)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)


def _tampered(env, type: str, payload: dict, key: str, value) -> None:
    """wa signs `type` into the hub space, then the row is edited without rehashing:
    parseable, but no admission rule accepts it."""
    log = EventLog(env.hub_dir, "wa", sign=True, keys_dir=env.keys_dir, registry_path=env.registry)
    log.append(type, payload)
    path = log.path if log.path.exists() else log.telemetry_path
    row = json.loads(path.read_text().strip())
    row["payload"][key] = value
    path.write_text(json.dumps(row) + "\n")


def test_r4_month_to_date_spend_counts_only_admitted_spend(env, monkeypatch):
    from claim_gate import month_to_date_cents
    _default_keys(env, monkeypatch)
    hub = env.hub()
    _tampered(env, "spend.record", {"cents": 50000}, "cents", 999999)
    assert sum(hub._state()[1].spend.values()) == 0
    assert month_to_date_cents(env.hub_dir, ["wa"]) == 0


def test_r4_creates_today_and_parent_hops_read_only_admitted_items(env, monkeypatch):
    from claim_gate import creates_today, parent_hops
    _default_keys(env, monkeypatch)
    hub = env.hub()
    _tampered(env, "item.create", {"id": "forged", "hops": 1}, "hops", 99)
    assert "forged" not in hub._state()[1].items
    assert creates_today(env.hub_dir, "wa") == 0
    assert parent_hops(env.hub_dir, "forged") is None


def test_r4_claim_gate_readers_keep_the_plain_path_without_a_hub(tmp_path):
    """Boundary: a space without a hub reads exactly as before."""
    from claim_gate import creates_today, month_to_date_cents, parent_hops
    plain = tmp_path / "plain"
    EventLog(plain, "wa", sign=False).append("item.create", {"id": "p1", "hops": 2})
    EventLog(plain, "wa", sign=False).append("spend.record", {"cents": 120})
    assert creates_today(plain, "wa") == 1
    assert parent_hops(plain, "p1") == 2
    assert month_to_date_cents(plain, ["wa"]) == 120


def test_r4_autofix_does_not_treat_an_inadmissible_item_as_an_existing_repair(env, monkeypatch):
    import time
    from types import SimpleNamespace
    from jobs import autofix
    _default_keys(env, monkeypatch)
    hub = env.hub()
    iid = f"{autofix.MARK}-audit-job-{time.strftime('%Y%m%d', time.gmtime())}"
    _tampered(env, "item.create", {"id": iid, "title": "original"}, "title", "tampered")
    assert iid not in hub._state()[1].items
    monkeypatch.setattr(autofix, "_space", lambda root: env.hub_dir)
    monkeypatch.setattr(autofix, "contract_sha", lambda *a: "fixture-contract")
    job = SimpleNamespace(name="audit-job", machine="fixture", delegate=True)
    state, detail = autofix.delegate(job, ["failed"], {"first_failed": "now"}, root=env.tmp,
                                     assignee="wa", dry=True)
    assert state != "exists", detail


_ADMISSION = {"authoritative_state", "authoritative_events", "_authoritative", "admit", "seal_status",
              "withdrawals"}
_WRAPPERS: dict = {}


def _fold_wrappers() -> dict:
    """{function name: modules defining it} for every production helper that
    folds (directly or through another such helper) what a caller passes it --
    `verify_seal`, `build_seal_payload`, `settled_events`, ... (Codex round 4:
    `verify_seal(read_events(space))` folded raw events and the guard did not
    see it). The admission path itself is not a wrapper."""
    import ast
    if _WRAPPERS:
        return _WRAPPERS
    lib = Path(hubmod.__file__).resolve().parents[1]
    funcs, imports = [], {}
    for p in sorted(lib.rglob("*.py")):
        if "tests" in p.parts:
            continue
        rel = p.relative_to(lib).with_suffix("").parts
        mod = ".".join(rel)
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        pkg = rel[:-1]
        names = {}                       # local name -> (module, attribute or None)
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                base = n.module or ""
                if n.level:
                    up = pkg[:len(pkg) - (n.level - 1)] if n.level > 1 else pkg
                    base = ".".join([*up, *([base] if base else [])])
                for a in n.names:
                    names[a.asname or a.name] = (base, a.name)
            elif isinstance(n, ast.Import):
                for a in n.names:
                    names[a.asname or a.name] = (a.name, None)
        imports[mod] = names
        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name not in _ADMISSION:
                funcs.append((mod, n))
    local = {}
    for mod, fn in funcs:
        local.setdefault(mod, set()).add(fn.name)

    def resolve(mod, func):
        """(module, name) a call resolves to, or None."""
        if isinstance(func, ast.Name):
            if func.id in local.get(mod, ()):
                return (mod, func.id)
            got = imports[mod].get(func.id)
            return (got[0], got[1]) if got and got[1] else None
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            got = imports[mod].get(func.value.id)
            if got:
                return (got[0] if got[1] is None else f"{got[0]}.{got[1]}", func.attr)
        return None

    # Per function and PARAMETER, once: the resolved calls (and argument
    # positions) that parameter reaches, through names assigned from it.
    params_of = {(mod, fn.name): [a.arg for a in fn.args.args] for mod, fn in funcs}
    facts = []
    for mod, fn in funcs:
        assigns = [a for a in ast.walk(fn) if isinstance(a, (ast.Assign, ast.AnnAssign, ast.For))
                   and (a.iter if isinstance(a, ast.For) else a.value) is not None]
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)]
        for i, p in enumerate(a.arg for a in fn.args.args + fn.args.kwonlyargs):
            tainted = {p}
            for _ in range(3):       # names assigned from the parameter carry it
                for a in assigns:
                    value = a.iter if isinstance(a, ast.For) else a.value
                    used = {x.id for x in ast.walk(value) if isinstance(x, ast.Name)}
                    admitted = any(isinstance(x, ast.Call) and getattr(x.func, "id", getattr(x.func, "attr", None))
                                   in _ADMISSION for x in ast.walk(value))
                    if used & tainted and not admitted:
                        targets = [a.target] if not isinstance(a, ast.Assign) else a.targets
                        tainted |= {x.id for t in targets for x in ast.walk(t) if isinstance(x, ast.Name)}
            reach = set()
            for c in calls:
                target = resolve(mod, c.func)
                if not target or target == (mod, fn.name):
                    continue
                for j, arg in enumerate(c.args):
                    if any(isinstance(x, ast.Name) and x.id in tainted for x in ast.walk(arg)):
                        reach.add((target, j))
                for k in c.keywords:
                    if k.arg and any(isinstance(x, ast.Name) and x.id in tainted for x in ast.walk(k.value)):
                        reach.add((target, k.arg))
            if reach:
                facts.append((mod, fn.name, i, p, reach))
    # found: {name: {module: positions (index or parameter name) that are folded}}
    found = {"fold": {"ledger.fold": {0, "events"}}}

    def folds_at(target, pos) -> bool:
        m, n = target
        got = found.get(n, {}).get(m)
        if not got:
            return False
        if pos in got:
            return True
        names = params_of.get(target, [])
        return (isinstance(pos, int) and pos < len(names) and names[pos] in got) or (
            isinstance(pos, str) and pos in names and names.index(pos) in got)

    changed = True
    while changed:
        changed = False
        for mod, name, i, p, reach in facts:
            have = found.get(name, {}).get(mod, set())
            if i in have:
                continue
            if any(folds_at(t, pos) for t, pos in reach):
                found.setdefault(name, {}).setdefault(mod, set()).update({i, p})
                changed = True
    _WRAPPERS.update(found)
    return _WRAPPERS


def _raw_folds(path: Path, lib: Path) -> list[str]:
    """Calls of `fold` (any alias) -- or of a helper that folds its argument
    (round 5) -- on events read with `read_events` (any alias), directly or
    through names assigned from them, in one function or at module level --
    unless the value went through the admission path first."""
    import ast
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    admission = _ADMISSION

    def bound(name: str) -> set:
        out = {name}
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom):
                out |= {a.asname or a.name for a in n.names if a.name == name}
        return out

    def imported(name: str, modules: set) -> set:
        """Local names under which the helper `name` of one of `modules` is
        reachable: `from M import name [as x]`, or `M.name` via an imported module.
        A same-named local function is not the helper."""
        out = set()
        stems = {m.rsplit(".", 1)[-1] for m in modules} | modules
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.module and (
                    n.module.lstrip(".") in modules or n.module.rsplit(".", 1)[-1] in stems):
                out |= {a.asname or a.name for a in n.names if a.name == name}
            if isinstance(n, (ast.ImportFrom, ast.Import)):
                for a in n.names:
                    if a.name in stems or a.name.rsplit(".", 1)[-1] in stems:
                        out.add(f"{a.asname or a.name}.{name}")
        return out

    folds, reads = bound("fold"), bound("read_events")
    #: helper call name -> the argument positions it folds (None: any argument)
    positions: dict = {n: None for n in folds}
    for helper, by_module in _fold_wrappers().items():
        if helper != "fold":
            for local_name in imported(helper, set(by_module)):
                positions[local_name] = set().union(*by_module.values())
    folds = set(positions)

    def called(expr, names):
        return [c for c in ast.walk(expr) if isinstance(c, ast.Call) and (
            (isinstance(c.func, ast.Name) and c.func.id in names)
            or (isinstance(c.func, ast.Attribute) and c.func.attr in names)
            or (isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
                and f"{c.func.value.id}.{c.func.attr}" in names))]

    def body_nodes(scope):
        """Nodes of `scope`, not of the functions nested in it (they are scopes of their own)."""
        stack = list(ast.iter_child_nodes(scope))
        while stack:
            n = stack.pop()
            yield n
            if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                stack.extend(ast.iter_child_nodes(n))

    found = []
    scopes = [tree] + [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for scope in scopes:
        nodes = list(body_nodes(scope))
        raw: set = set()

        def tainted(expr):
            if called(expr, admission):
                return False
            # A comprehension's own loop variable is local to it: `[e for e in admitted]`
            # is as clean as `admitted`, whatever `e` names elsewhere in the function.
            local = {n.id for c in ast.walk(expr) if isinstance(c, ast.comprehension)
                     for n in ast.walk(c.target) if isinstance(n, ast.Name)}
            return bool(called(expr, reads)) or any(isinstance(n, ast.Name) and n.id in raw - local
                                                    for n in ast.walk(expr))

        assigns = [n for n in nodes if isinstance(n, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.For))]
        changed = True
        while changed:
            changed = False
            for a in assigns:
                value = a.iter if isinstance(a, ast.For) else a.value
                if value is None or not tainted(value):
                    continue
                targets = [a.target] if not isinstance(a, ast.Assign) else a.targets
                for t in targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name) and n.id not in raw:
                            raw.add(n.id)
                            changed = True
        def folded_args(call):
            f = call.func
            key = (f.id if isinstance(f, ast.Name) else
                   f"{f.value.id}.{f.attr}" if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                   and f"{f.value.id}.{f.attr}" in positions else
                   f.attr if isinstance(f, ast.Attribute) else None)
            pos = positions.get(key, None) if key in positions else set()
            if pos is None:
                return list(call.args) + [k.value for k in call.keywords]
            return ([a for j, a in enumerate(call.args) if j in pos]
                    + [k.value for k in call.keywords if k.arg in pos])

        for n in nodes:
            if isinstance(n, ast.Call) and n in called(n, folds)[:1] and any(
                    tainted(x) for x in folded_args(n)):
                found.append(f"{path.relative_to(lib)}:{n.lineno}")
    return found


#: Folds of raw events that are not a read of a live space, each with why.
_RAW_FOLD_ALLOWED = {
    # restore() folds the chains SAVED in a checkpoint document into a scratch
    # directory (no hub config there) and compares the result with the root
    # the document recorded; a mismatch refuses the restore.
    "ledger_checkpoint.py",
    # The activation dry run (round 6) folds a space the way every reader
    # sees it TODAY (no hub yet) to compare with the would-be hub state; it
    # writes nothing and decides nothing.
    "ledger/activation.py",
}


def test_r4_no_production_module_folds_raw_events_in_any_shape():
    """Codex round 3: the line-regex guard missed aliased shapes such as
    `_fold(_read_events(space))` and folds of a filtered comprehension. This one
    reads the code (AST): any fold of what read_events returned, under any
    alias or through any name, outside the admission path, fails."""
    lib = Path(hubmod.__file__).resolve().parents[1]
    offenders = [hit for p in sorted(lib.rglob("*.py")) if "tests" not in p.parts
                 and str(p.relative_to(lib)) not in _RAW_FOLD_ALLOWED for hit in _raw_folds(p, lib)]
    assert offenders == [], offenders


def test_r4_the_shape_guard_catches_the_shapes_codex_named(tmp_path):
    """The guard is itself tested: each shape below is a bypass it must report."""
    lib = tmp_path / "lib"
    lib.mkdir()
    shapes = {
        "aliased.py": "from ledger.fold import fold as _fold\nfrom ledger.log import read_events as _read_events\n"
                      "def f(space):\n    return _fold(_read_events(space)).items\n",
        "filtered.py": "from ledger.fold import fold\nfrom ledger.log import read_events\n"
                       "def f(space):\n    events = [e for e in read_events(space) if e.type == 'spend.record']\n"
                       "    return sum(fold(events).spend.values())\n",
        "admitted.py": "from ledger.fold import fold\nfrom ledger.log import read_events\n"
                       "from ledger.hub import authoritative_events\n"
                       "def f(space):\n    events, kw = authoritative_events(space, read_events(space))\n"
                       "    return fold(events, **kw)\n",
    }
    for name, text in shapes.items():
        (lib / name).write_text(text)
    hits = {name: _raw_folds(lib / name, lib) for name in shapes}
    assert hits["aliased.py"] and hits["filtered.py"], hits
    assert hits["admitted.py"] == [], hits


def test_r4_decision_gates_never_read_raw_events():
    """creates_today and parent_hops never folded: they scanned raw rows. The
    claim and repair gates take events only from the admission path."""
    import ast
    lib = Path(hubmod.__file__).resolve().parents[1]
    for rel in ("claim_gate.py", "jobs/autofix.py"):
        tree = ast.parse((lib / rel).read_text(encoding="utf-8"))
        calls = [n.lineno for n in ast.walk(tree) if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Name) and n.func.id.lstrip("_") in ("read_events", "read"))
            or (isinstance(n.func, ast.Attribute) and n.func.attr == "read_events"))]
        assert calls == [], (rel, calls)


# ── round 4: recovery (Codex round-3 verdict, High/Medium findings) ─────────

# K (invalid_resolution): only an EFFECTIVE result or release resolves an uncertain attempt.

def test_round4_a_release_the_fold_ignores_does_not_resolve_an_uncertain_attempt(env):
    hub = env.hub(eligibility=None)                      # production assignee eligibility
    t = hubmod.LocalTransport(hub)
    own, wa, wb = env.writer("own", t), env.writer("wa", t), env.writer("wb", t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    lease = _lease_payload(wa.request_lease("t"))
    env.clock.advance(901)
    hub.expire_due()
    own.append("item.update", {"id": "t", "assignee": "wb"})
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t")
    assert exc.value.code == "uncertain_attempt", exc.value
    # The right attempt, the WRONG task content: the fold makes it a no-op.
    bad = _signed_into(env, wa, "item.release", {"id": "t", "attempt": lease["attempt"], "task_hash": "wrong"})
    hub.append(wa._submission(bad))
    assert hub._state()[1].outcomes[bad.hash].startswith("no-op"), hub._state()[1].outcomes[bad.hash]
    fresh = hubmod.sign_request("wb", "lease.request", {"space": _space_id(env.hub_dir), "hub": HUB_ID, "item": "t"},
                                keys_dir=env.keys_dir, clock=env.clock)
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.request_lease(fresh)
    assert exc.value.code == "uncertain_attempt", exc.value
    # Only the owner's signed resolution (or an effective result) frees it now.
    hubmod.resolve_attempt(env.hub_dir, "t", lease["attempt"], by=OWNER, reason="wa's release named other content",
                           keys_dir=env.keys_dir, registry_path=env.registry)
    again = hubmod.sign_request("wb", "lease.request", {"space": _space_id(env.hub_dir), "hub": HUB_ID, "item": "t"},
                                keys_dir=env.keys_dir, clock=env.clock)
    assert hub.request_lease(again)["status"] == "granted"


# L (lost_both): a space restored from before a grant AND lost private state is not a first start.

def test_round4_a_restore_from_before_a_grant_with_lost_private_state_runs_the_barrier(env):
    hub = env.hub(eligibility=None)
    assert not hub.barrier                                # a genuine first start has no barrier
    t = hubmod.LocalTransport(hub)
    own, wa = env.writer("own", t), env.writer("wa", t)
    own.append("item.create", {"id": "t", "assignee": "wa"})
    stale = env.tmp / "stale-restore"
    git_pull(env.hub_dir, stale)                         # a copy taken before the grant
    first = _lease_payload(wa.request_lease("t"))
    hub.close()
    restored = env.hub(space=stale, state="new-private-state", eligibility=None)
    assert "state_lost" in restored.barrier, restored.barrier
    again = env.writer("wa", hubmod.LocalTransport(restored))
    with pytest.raises(hubmod.HubRefused) as exc:
        again.request_lease("t")
    assert exc.value.code == "recovering", exc.value
    assert first["expires_ms"] > env.clock()             # the first attempt may still be running
    # The owner's signed recovery transition is the only way out.
    restored.clear_recovery(hubmod.sign_request(OWNER, "recovery.clear", {"hub": HUB_ID, "space": _space_id(stale)},
                                                keys_dir=env.keys_dir, clock=env.clock))
    assert "state_lost" not in restored.barrier


# M (stale_retry): an exact retry of an answered request gets its answer, however late.

def test_round4_an_exact_retry_after_the_request_window_gets_the_original_answer(env):
    hub = env.hub()
    t = hubmod.LocalTransport(hub)
    own = env.writer("own", t)
    env.writer("wa", t)                                  # wa's key
    own.append("item.create", {"id": "t", "assignee": "wa"})
    own.append("item.create", {"id": "u", "assignee": "wa"})
    body = {"space": _space_id(env.hub_dir), "hub": HUB_ID}
    req = hubmod.sign_request("wa", "lease.request", {**body, "item": "t"}, keys_dir=env.keys_dir, clock=env.clock)
    unsent = hubmod.sign_request("wa", "lease.request", {**body, "item": "u"}, keys_dir=env.keys_dir, clock=env.clock)
    first = hub.request_lease(req)
    assert first["status"] == "granted"
    env.clock.advance(301)
    assert first["lease"]["payload"]["expires_ms"] > env.clock()
    assert hub.request_lease(req) == first               # idempotent: the same signed answer
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.request_lease(unsent)                        # a NEW request keeps the freshness rule
    assert exc.value.code == "stale_request", exc.value


# N (ignored_stop_witness): the hub honours a writer log's stop record and rewind witness.

def test_round4_the_hub_appends_nothing_onto_a_stopped_or_rewound_log(env):
    from ledger.log import stop_path, witness_path
    hub = env.hub()
    own = env.writer("own", hubmod.LocalTransport(hub))
    own.append("item.create", {"id": "t"})
    target = env.hub_dir / ".datacore" / "events" / "own.jsonl"
    before = target.read_bytes()
    marker = stop_path(target)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("owner must clear this stopped log\n")
    e = _signed_into(env, own, "item.update", {"id": "t", "title": "past the stop"})
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.append(own._submission(e))
    assert exc.value.code == "stale_log", exc.value
    assert marker.exists() and target.read_bytes() == before
    # A rewound log (its witness is ahead of the file) is stopped too, and the stop is recorded.
    marker.unlink()
    witness_path(target).write_text("7")
    with pytest.raises(hubmod.HubRefused) as exc:
        hub.append(own._submission(e))
    assert exc.value.code == "stale_log", exc.value
    assert marker.exists() and target.read_bytes() == before
