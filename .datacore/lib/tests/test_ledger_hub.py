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
        hubmod.enable(self.hub_dir, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900)
        self.hubs: list = []

    def hub(self, actor: str = GRANTOR, space: Path | None = None, *, eligibility=allow_any,
            state: str = "state", policy=None) -> "hubmod.Hub":
        # A fixed policy, not the installation's file: the hub's front door
        # applies policy (audit finding 12), and a test must not depend on
        # whatever approvals_policy.yaml this machine happens to carry.
        policy = policy if policy is not None else TEST_POLICY
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
                                clock=self.clock)

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
    lease_a = wa.request_lease("t1")

    # Partition: a second checkout that never saw hub_a's lease is promoted by
    # the owner (an explicit act) and grants the same item to wb.
    b_dir = env.tmp / "hub-b-space"
    shutil.copytree(env.hub_dir, b_dir)
    (b_dir / ".datacore" / "events" / f"{GRANTOR}.jsonl").unlink()
    hubmod.promote(b_dir, "hubb", by="owner", reason="test partition")
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
    # expired while nobody could renew it, so the restart grace refuses it.
    env.clock.advance(60)
    hub2 = env.hub(state="state2")
    t.hub, t.down = hub2, False
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code in ("restart_grace", "taken")

    replayed = wa.replay()
    assert [r["status"] for r in replayed] == ["accepted"] * 3
    assert wa.outage() is None
    with pytest.raises(hubmod.HubRefused) as exc:
        wb.request_lease("t1")
    assert exc.value.code == "closed"

    # No event lost: the hub's copy of wa's log is wa's log, line for line.
    local = (wa.space_dir / ".datacore" / "events" / "wa.jsonl").read_bytes()
    assert (env.hub_dir / ".datacore" / "events" / "wa.jsonl").read_bytes() == local
    # No double run: one lease, one completion, no conflict.
    assert [e.payload["holder"] for e in leases_in(env.hub_dir)] == ["wa"]
    item = fold(read_events(env.hub_dir)).items["t1"]
    assert item.status == "completed" and item.owner == "wa" and not item.lease_conflicts


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
    cfg = hubmod.promote(standby_dir, "hubstandby", by="owner", reason="primary retired")
    assert cfg.grantor == "hubstandby" and cfg.promoted_by == "owner"
    # The writer learns of the promotion the way every copy does: it pulls the
    # committed config. Until then it refuses the new grantor's answers
    # (audit finding 5: first answers are pinned to the grantor it knows).
    shutil.copyfile(standby_dir / hubmod.CONFIG, wa.space_dir / hubmod.CONFIG)
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
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900)
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
    head = read_events(space)[-1]
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
    hubmod.enable(space, hub_id=HUB_ID, grantor=GRANTOR, lease_ttl_s=900)
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

OWNER = "own"


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
                            state_dir=env.tmp / "wstate" / f"{actor}-{space.name}", clock=env.clock)


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
