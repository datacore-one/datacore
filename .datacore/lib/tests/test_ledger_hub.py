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
            state: str = "state") -> "hubmod.Hub":
        h = hubmod.Hub(space or self.hub_dir, actor, keys_dir=self.keys_dir, registry_path=self.registry,
                       state_dir=self.tmp / state / actor, clock=self.clock, eligibility=eligibility)
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
    """Drop the old seal and seal the tampered log again with the hub's key."""
    hub_log = git / ".datacore" / "events" / f"{GRANTOR}.jsonl"
    lines = [ln for ln in hub_log.read_text().splitlines() if from_line(ln).type != "ledger.seal"]
    hub_log.write_text("\n".join(lines) + "\n")
    shutil.rmtree(env.tmp / "reseal-state", ignore_errors=True)
    h = hubmod.Hub(git, GRANTOR, keys_dir=env.keys_dir, registry_path=env.registry,
                   state_dir=env.tmp / "reseal-state", clock=env.clock, eligibility=allow_any)
    h.seal()
    h.close()


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
