"""Round 8 of the ledger hub: POLICY VERSIONS (owner decision 1, 2026-10-23;
closes the round-7 audit's R10).

  Every policy change is a ledger record signed by the owner; each event is
  judged by the policy in force when the hub admitted it. A later policy
  change never changes an earlier outcome.

How it is built: the owner's `hub.enable` records the policy in force at
activation; each later change is an owner-signed `policy.set` in the owner's
log, which takes effect only when the active grantor admits it -- its
`ledger.admit` records the frontier (each log's highest seq the hub held).
An event at or below the frontier was admitted under the earlier policy; one
above it, under the new one. Every reader takes the policy from these records
alone, never from the policy file, so every replica agrees.

The first test is the round-7 auditor's R10 probe with its assertions turned
around; it was seen failing on 8f98410f.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _replica, env  # noqa: F401
from ledger import hub as H
from ledger.events import to_line
from ledger.log import EventLog, read_events

INITIAL = None
try:
    from ledger.hub_policy import PolicyInputs
    INITIAL = PolicyInputs(approver=T.APPROVER, cosign_effects=frozenset(), known_effects=frozenset({"publish"}))
    CHANGED = replace(INITIAL, cosign_effects=frozenset({"publish"}))
except ImportError:  # pragma: no cover
    pass


@pytest.fixture
def penv(tmp_path, monkeypatch):
    """A hub space activated under INITIAL (the owner's hub.enable records it)."""
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "runtime"))
    monkeypatch.setattr(T, "TEST_POLICY", INITIAL)
    e = T.Env(tmp_path, monkeypatch)
    yield e
    e.close()


def _reader(env, h, policy, name):
    return H.HubWriter(env.tmp / "own-space", "own", H.LocalTransport(h), keys_dir=env.keys_dir,
                       registry_path=env.registry, state_dir=env.tmp / name, clock=env.clock, policy=policy)


def _status(writer, env, e):
    return writer.receipt_check(env.hub_dir, required=[e.hash])["status"]


def _set_policy(env, policy, reason="eval"):
    return H.set_policy(env.hub_dir, policy, by=T.OWNER, reason=reason, keys_dir=env.keys_dir,
                        registry_path=env.registry)


# ── R10: a policy file edited after the fact changes nothing ────────────────

def test_r8_an_edited_policy_file_never_changes_a_confirmed_outcome(penv):
    env = penv
    h = env.hub(policy=INITIAL)
    own = env.writer("own", H.LocalTransport(h))
    event, _ = own.append("item.create", {"id": "t", "effects": ["publish"]})
    h.seal()
    assert _status(own, env, event) == "confirmed"
    h.close()
    # The file now says `publish` needs a co-sign; nobody recorded that change.
    h = env.hub(policy=CHANGED, record_policy=False)
    reader = _reader(env, h, CHANGED, "policy-reader")
    assert _status(reader, env, event) == "confirmed", "an unrecorded policy edit changed a confirmed creation"
    h.seal()
    assert _status(reader, env, event) == "confirmed"
    assert "t" in h._state()[1].items
    replica = _replica(env, env.hub_dir)
    assert "t" in H.authoritative_state(replica, registry_path=env.registry, policy=CHANGED).items


def test_r8_a_recorded_policy_change_applies_forward_only(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    before, _ = own.append("item.create", {"id": "t", "effects": ["publish"]})
    h.seal()
    assert _status(own, env, before) == "confirmed"
    _set_policy(env, CHANGED)
    h.seal()                                   # the grantor admits the change, then seals
    admits = [e for e in read_events(env.hub_dir) if e.type == "ledger.admit"
              and (e.payload or {}).get("kind") == "policy"]
    assert len(admits) == 1 and isinstance(admits[0].payload.get("frontier"), dict)
    # The earlier creation keeps its outcome, on the hub and on every replica.
    assert _status(own, env, before) == "confirmed"
    assert "t" in h._state()[1].items
    # A creation after the change is judged by the new policy: refused at the door.
    with pytest.raises(H.HubRefused) as exc:
        own.append("item.create", {"id": "t2", "effects": ["publish"]})
    assert exc.value.code == "not_authorized"
    h.seal()
    assert _status(own, env, before) == "confirmed"
    for policy in (INITIAL, CHANGED, T.TEST_POLICY, None):
        state = H.authoritative_state(_replica(env, env.hub_dir), registry_path=env.registry, policy=policy)
        assert "t" in state.items and "t2" not in state.items


def test_r8_a_git_arrival_after_the_change_is_judged_by_the_new_policy(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "seed"})
    # wb writes a creation the hub has never seen (its log is not in the hub yet).
    wb_space = env.tmp / "wb-space"
    (wb_space / ".datacore" / "events").mkdir(parents=True)
    T.git_pull(env.hub_dir, wb_space)
    late = T._signed_into(env, env.writer("wb", H.LocalTransport(h)), "item.create",
                          {"id": "late", "effects": ["publish"]})
    _set_policy(env, CHANGED)
    h.seal()
    T.git_pull(wb_space, env.hub_dir)          # arrives after the change: above the frontier
    h.seal()
    assert "late" not in h._state()[1].items, "a git arrival after the change was judged by the old policy"


def test_r8_a_policy_record_by_anyone_but_the_owner_has_no_effect(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    with pytest.raises(H.HubRefused):
        own.append("policy.set", {"space": h.config().space_id, "hub": T.HUB_ID, "policy": CHANGED.to_record(),
                                  "reason": "not mine to set"})
    # Written straight into a plain log, signed, it is still not the owner's record.
    EventLog(env.hub_dir, "wa", keys_dir=env.keys_dir, registry_path=env.registry, sign=True,
             hub=T.HUB_ID).append("policy.set", {"space": h.config().space_id, "hub": T.HUB_ID,
                                                 "policy": CHANGED.to_record(), "reason": "forged"})
    h.seal()
    assert own.append("item.create", {"id": "t", "effects": ["publish"]})[1]["status"] == "accepted"
    h.seal()
    assert "t" in h._state()[1].items


def test_r8_an_owner_record_the_grantor_never_admitted_has_no_effect(penv):
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t", "effects": ["publish"]})
    h.seal()
    h.close()
    replica = _replica(env, env.hub_dir)
    # The owner's record reaches a replica that the grantor never saw it on.
    _set_policy(env, CHANGED)
    owner_log = env.hub_dir / ".datacore" / "events" / f"{T.OWNER}.jsonl"
    (replica / ".datacore" / "events" / f"{T.OWNER}.jsonl").write_bytes(owner_log.read_bytes())
    assert "t" in H.authoritative_state(replica, registry_path=env.registry).items


def test_r8_enable_records_the_policy_and_a_record_without_one_is_refused(penv):
    env = penv
    enable = next(e for e in read_events(env.hub_dir) if e.type == "hub.enable")
    assert PolicyInputs.from_record(enable.payload["policy"]) == INITIAL
    # A hub.enable without a policy (an older format) is not read around the policy.
    space = env.tmp / "no-policy-space"
    (space / ".datacore" / "events").mkdir(parents=True)
    monkey_enable = H.enable(space, hub_id="hub-np", grantor=T.GRANTOR, owner=T.OWNER, keys_dir=env.keys_dir,
                             registry_path=env.registry, policy=INITIAL)
    assert monkey_enable.hub_id == "hub-np"
    owner_log = space / ".datacore" / "events" / f"{T.OWNER}.jsonl"
    rec = next(e for e in read_events(space) if e.type == "hub.enable")
    stripped = {k: v for k, v in rec.payload.items() if k != "policy"}
    from ledger.keys import sign
    from ledger.events import canonical_bytes, compute_hash, event_body
    new = replace(rec, payload=stripped, hash="", sig="")
    new = replace(new, hash=compute_hash(event_body(new)))
    new = replace(new, sig=sign(T.OWNER, canonical_bytes(event_body(new)), keys_dir=env.keys_dir))
    owner_log.write_text(to_line(new) + "\n")
    with pytest.raises(H.HubRefused) as exc:
        H.authoritative_state(space, registry_path=env.registry, policy=INITIAL)
    assert exc.value.code == "no_policy_record"


def test_r8_status_reports_a_policy_file_that_differs_from_the_ledger(penv):
    env = penv
    h = env.hub(policy=CHANGED, record_policy=False)
    st = h.status()
    assert st["policy"]["recorded_matches_configured"] is False
    h.close()
    h = env.hub(policy=INITIAL)
    assert h.status()["policy"]["recorded_matches_configured"] is True


# ── the Lean model's finding (HubFinality.policy_frontier, round 8) ─────────

def test_r8_a_policy_admitted_while_a_sealed_log_is_missing_never_rejudges_it(penv):
    """A hub that holds less than an earlier seal covered (a restore that lost
    a writer's log) must not record a policy frontier below that sealed event:
    when the log comes back, the sealed creation keeps its outcome."""
    import shutil
    env = penv
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    event, _ = own.append("item.create", {"id": "t", "effects": ["publish"]})
    h.seal()
    assert _status(own, env, event) == "confirmed"
    log = env.hub_dir / ".datacore" / "events" / "own.jsonl"
    saved = env.tmp / "own.jsonl.bak"
    shutil.move(log, saved)                     # a restore that lost own's log
    _set_policy(env, CHANGED)
    h._judge_policies()
    shutil.move(saved, log)                     # the log comes back
    h.seal()
    assert _status(own, env, event) == "confirmed", "a policy change re-judged a sealed creation"
    assert "t" in h._state()[1].items


# ── the round-8 independent audit (Codex, test_r8_probes.py), turned around ─

def test_r8_f8_1_a_successor_missing_predecessor_history_defers_a_policy_change(penv):
    """F8-1: after a promotion, a copy that lost the old grantor's and a sealed
    writer's logs must not admit a policy change with a frontier below what
    the predecessor sealed; when the logs return, the sealed outcome stands."""
    e = penv
    first = e.hub()
    own = e.writer("own", H.LocalTransport(first))
    event, _ = own.append("item.create", {"id": "t", "effects": ["publish"]})
    first.seal()
    assert _status(own, e, event) == "confirmed"
    first.fence("handover")
    H.promote(e.hub_dir, "hubb", by=T.OWNER, reason="handover", keys_dir=e.keys_dir,
              registry_path=e.registry, clock=e.clock)
    saved = {}
    for actor in (T.GRANTOR, "own"):
        path = e.hub_dir / ".datacore/events" / f"{actor}.jsonl"
        saved[path] = path.read_bytes()
        path.unlink()
    _set_policy(e, CHANGED)
    successor = e.hub("hubb")
    successor._judge_policies()
    admits = [x for x in read_events(e.hub_dir) if x.type == "ledger.admit" and x.payload.get("kind") == "policy"]
    assert all(a.payload["frontier"].get("own", -1) >= event.seq for a in admits), admits
    for path, data in saved.items():
        path.write_bytes(data)
    successor.seal()
    assert _status(own, e, event) == "confirmed", "a policy change re-judged the predecessor's sealed creation"
    assert "t" in successor._state()[1].items


def test_r8_f8_2_a_hub_never_seals_less_than_it_sealed_before(env):
    """F8-2: a hub that lost a sealed claimant's log refuses to seal less than
    its earlier seal covered, and an earlier-stamped rival accepted meanwhile
    never displaces the sealed claim once the log returns."""
    import time
    h = env.hub()
    own, wa, wb = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa", "wb")]
    ms = int(time.time() * 1000)
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{ms:013d}.0000.own")))
    original = T._signed_into(env, wa, "item.claim", {"id": "t"}, hlc=f"{ms + 200:013d}.0000.wa")
    h.append(wa._submission(original))
    h.seal()
    path = env.hub_dir / ".datacore/events/wa.jsonl"
    saved = path.read_bytes()
    path.unlink()
    with pytest.raises(H.HubRefused) as exc:
        h.seal()
    assert exc.value.code == "behind_seal", exc.value
    late = T._signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{ms + 100:013d}.0000.wb")
    try:
        h.append(wb._submission(late))
    except H.HubRefused:
        pass
    path.write_bytes(saved)
    h.seal()
    assert wa.receipt_check(env.hub_dir, required=[original.hash])["status"] == "confirmed"
    assert wb.receipt_check(env.hub_dir, required=[late.hash])["status"] != "confirmed"
    assert h._state()[1].items["t"].owner == "wa"


def test_r8_f8_3_an_unadmitted_line_never_moves_the_policy_frontier(penv):
    """F8-3: the frontier is what the hub ADMITTED, never a raw line: an
    unsigned out-of-chain seq 1000 must not let a later valid event of that log
    be judged by the superseded policy."""
    from dataclasses import replace as _replace
    e = penv
    h = e.hub()
    wa = e.writer("wa", H.LocalTransport(h))
    seed = T._signed_into(e, wa, "item.create", {"id": "seed"})
    h.append(wa._submission(seed))
    path = e.hub_dir / ".datacore/events/wa.jsonl"
    with path.open("a") as f:
        f.write(to_line(_replace(seed, seq=1000, sig="")) + "\n")
    _set_policy(e, CHANGED)
    h._judge_policies()
    admits = [x for x in read_events(e.hub_dir) if x.type == "ledger.admit" and x.payload.get("kind") == "policy"]
    assert admits and admits[-1].payload["frontier"].get("wa") == seed.seq, admits
    new = T._signed_into(e, wa, "item.create", {"id": "forbidden", "effects": ["publish"]})
    with path.open("a") as f:
        f.write(to_line(new) + "\n")
    h.seal()
    assert "forbidden" not in h._state()[1].items
    assert wa.receipt_check(e.hub_dir, required=[new.hash])["status"] != "confirmed"


def test_r8_writer_pre_checks_ask_the_ledgers_policy(penv):
    """The round-8 audit's residual: a writer-side pre-check must judge by the
    policy the hub will apply (the ledger's latest version), not its own file."""
    env = penv
    h = env.hub()
    assert H.policy_in_force(env.hub_dir, registry_path=env.registry, policy=CHANGED) == INITIAL
    _set_policy(env, CHANGED)
    h.seal()
    assert H.policy_in_force(env.hub_dir, registry_path=env.registry, policy=INITIAL) == CHANGED


# ── the round-8 targeted re-check (Codex, test_r8_recheck.py), turned around ─

@pytest.mark.parametrize("lost", ["grantor-only"])
def test_r8e_an_attested_promotion_needs_the_predecessors_log(penv, lost):
    """A promotion the owner attests from a copy that lacks the stopped
    grantor's log would name no last seal and cut its sealed history off for
    good; it is refused until the log is restored. (A copy that lost the
    grantor's log AND every log it served holds no trace of the service; that
    case rests on the owner's attestation and is reported, not tested.)"""
    e = penv
    first = e.hub()
    own = e.writer("own", H.LocalTransport(first))
    event, _ = own.append("item.create", {"id": "t", "effects": ["publish"]})
    first.seal()
    first.close()
    actors = (T.GRANTOR, "own") if lost == "grantor-and-writer" else (T.GRANTOR,)
    saved = {}
    for actor in actors:
        path = e.hub_dir / ".datacore/events" / f"{actor}.jsonl"
        saved[path] = path.read_bytes()
        path.unlink()
    with pytest.raises(H.HubRefused) as exc:
        H.promote(e.hub_dir, "hubb", by=T.OWNER, reason="recover a stopped hub from a partial copy",
                  attest_stopped=True, keys_dir=e.keys_dir, registry_path=e.registry, clock=e.clock)
    assert exc.value.code == "behind_predecessor", exc.value
    for path, data in saved.items():
        path.write_bytes(data)
    H.promote(e.hub_dir, "hubb", by=T.OWNER, reason="recover a stopped hub", attest_stopped=True,
              keys_dir=e.keys_dir, registry_path=e.registry, clock=e.clock)
    e.clock.advance(1000)
    _set_policy(e, CHANGED)
    successor = e.hub("hubb")
    successor.seal()
    assert _status(own, e, event) == "confirmed"
    assert "t" in successor._state()[1].items


def test_r8e_scanning_readers_never_count_a_rejected_proposal(env, monkeypatch):
    """Spend, the daily creation count and the autofix scan read events, not
    the fold: a record the hub kept as a rejected proposal counts for none."""
    import datetime as dt
    import ledger.keys as keys
    import claim_gate
    from jobs import autofix
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    monkeypatch.setattr(claim_gate, "principal_of", lambda actor: (actor, {}))
    h = env.hub()
    own, wa = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa")]
    ms = T._real_ms()
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t", "autofix": True},
                                            hlc=f"{ms:013d}.0000.own")))
    h.seal()
    day = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).date()
    for i, (type_, payload) in enumerate((("spend.record", {"id": "t", "cents": 12345}),
                                          ("item.create", {"id": "t"}),
                                          ("item.release", {"id": "t", "kind": "waiting_on_owner"}))):
        # stamped at or before t's sealed history: the hub keeps each as a rejected proposal
        bad = T._signed_into(env, wa, type_, payload, hlc=f"{ms - 2 + i:013d}.0000.own")
        assert h.append(wa._submission(bad, chain="wa"))["status"] == "rejected"
    assert claim_gate.month_to_date_cents(env.hub_dir, ["wa"], today=day) == 0
    assert claim_gate.creates_today(env.hub_dir, "wa", today=day) == 0
    monkeypatch.setattr(autofix, "_space", lambda root: env.hub_dir)
    assert not any(r.get("waiting_on_owner") for r in autofix.repairs(env.tmp))


def test_r8e_a_broken_local_policy_file_does_not_block_a_hub_space(penv, monkeypatch):
    """In a hub space the policy is the ledger's; the local file is not read."""
    from ledger import policy
    e = penv
    h = e.hub()
    own = e.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t"})
    h.seal()
    path = e.tmp / "bad-local-policy.yaml"
    path.write_text("version: 1\napprover: []\ncosign_effects: []\n")
    monkeypatch.setattr(policy, "DEFAULT_POLICY_PATH", path)
    assert "t" in H.authoritative_state(e.hub_dir, registry_path=e.registry).items
