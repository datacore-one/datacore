"""Round 7 of the ledger hub: the final round-6 audit's reproductions (F1-F5,
2026-10-23 final audit verdict) turned into regression tests, under the
owner's round-7 decision:

  CONTROL RECORDS ARE NEVER REVERSIBLE. Only ordinary item (task) events can
  be reversed, inside the correction window. Everything else -- admission
  decisions, seals, hub records, key rotations, approvals, ownership and
  principal records, compensations, leases and grants, anything that
  authorises or gates another record -- is corrected only by a new forward
  record. A reversal naming one is refused at write time and has no effect
  in the fold if it arrives by import, deterministically.

Each test was written from the auditor's probe (test_final_probes.py) with
the assertion turned around, and seen failing on the round-6 heads.
"""
from __future__ import annotations

import ast
import time
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _confirmed, _replica, _void, env  # noqa: F401
from ledger import hub as H
from ledger.events import to_line
from ledger.log import read_events

LIB = Path(__file__).resolve().parents[1]


def _rejected_hashes(h):
    return {x for (_, _, x) in h._rejected()}


def _refused_or_rejected(call) -> str:
    """The code a hub refusal carried; a refused submission never counts."""
    try:
        r = call()
    except H.HubRefused as exc:
        return exc.code
    assert (r or {}).get("status") == "rejected", r
    return str((r or {}).get("reason", "")).split(":", 1)[0]


# ── F1: a promotion never changes a predecessor's sealed confirmed claim ────

@pytest.mark.parametrize("arrival", ["append", "git"])
def test_r7_promotion_keeps_the_predecessors_sealed_claim(env, arrival):
    h = env.hub()
    own, wa, wb = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa", "wb")]
    base = int(time.time() * 1000)
    create = T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{base:013d}.0000.own")
    h.append(own._submission(create))
    a = T._signed_into(env, wa, "item.claim", {"id": "t"}, hlc=f"{base + 200:013d}.0000.wa")
    h.append(wa._submission(a))
    h.seal()
    assert wa.receipt_check(env.hub_dir, required=[a.hash])["status"] == "confirmed"
    h.fence("review promotion")
    H.promote(env.hub_dir, "hubb", by=T.OWNER, reason="review promotion", keys_dir=env.keys_dir,
              registry_path=env.registry, attest_stopped=True, clock=env.clock)
    new = env.hub("hubb")
    b = T._signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{base + 100:013d}.0000.wb")
    if arrival == "append":
        assert _refused_or_rejected(lambda: new.append(wb._submission(b))) == "sealed_late"
    else:
        T.git_pull(wb.space_dir, env.hub_dir)
    new.seal()
    assert b.hash in _rejected_hashes(new)
    assert wa.receipt_check(env.hub_dir, required=[a.hash])["status"] == "confirmed"
    assert new._state()[1].items["t"].owner == "wa"
    assert wb.receipt_check(env.hub_dir, required=[b.hash])["status"] != "confirmed"
    # The same on a replica rebuilt from the published logs.
    replica = _replica(env, env.hub_dir)
    assert H.authoritative_state(replica, registry_path=env.registry, policy=T.TEST_POLICY).items["t"].owner == "wa"


def test_r7_late_guard_reads_every_grantor_in_the_chain(env):
    """The guard's seals are those of every grantor of the space (all
    predecessors), not only the active grantor's own (code-shape check)."""
    src = (LIB / "ledger" / "hub.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef) and n.name == "_sealed_late")
    body = ast.get_source_segment(src, fn)
    assert "grantors" in body, "the late-event guard must read every grantor's seals"


# ── F2: hub records without the config are read fail-closed ────────────────

def _logs_only_replica(env):
    h = env.hub()
    own = env.writer("own", H.LocalTransport(h))
    own.append("item.create", {"id": "t", "assignee": "wa"})
    wb = env.writer("wb", None)
    fake = replace(T._signed_into(env, wb, "item.claim", {"id": "t"}), sig="")
    (wb.space_dir / ".datacore/events/wb.jsonl").write_text(to_line(fake) + "\n")
    T.git_pull(wb.space_dir, env.hub_dir)
    assert h._state()[1].items["t"].owner is None
    replica = _replica(env, env.hub_dir)
    (replica / H.CONFIG).unlink()                    # the logs survive, the config cache is lost
    assert any(e.type == "hub.enable" for e in read_events(replica))
    return replica


def test_r7_logs_only_rebuild_without_config_is_read_fail_closed(env):
    replica = _logs_only_replica(env)
    with pytest.raises(H.HubUnavailable):
        H.authoritative_state(replica, registry_path=env.registry, policy=T.TEST_POLICY)
    with pytest.raises(H.HubUnavailable):
        H.authoritative_events(replica, registry_path=env.registry, policy=T.TEST_POLICY)
    with pytest.raises(H.HubUnavailable):
        H.strict_problems(replica, registry_path=env.registry)


def test_r7_logs_only_rebuild_excuses_nothing_in_the_void_reader(env):
    from ledger import voids
    replica = _logs_only_replica(env)
    assert len(voids._hub_space_voids(replica / ".datacore" / "events", registry_path=env.registry)) == 0
    assert voids._hub_space_voids(replica / ".datacore" / "events", registry_path=env.registry) is not None


def test_r7_logs_only_rebuild_refuses_the_decision_readers(env, monkeypatch):
    from ledger import keys
    replica = _logs_only_replica(env)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    with pytest.raises(H.HubUnavailable):
        keys.recorded_rotations(replica, "wa")


def test_r7_an_unsigned_hub_enable_in_a_plain_space_does_not_block_reads(tmp_path):
    """Fail-closed is decided by a SIGNED owner record; a bare line of that
    type in a space without a hub changes nothing (no denial by forgery)."""
    from ledger.log import EventLog
    space = tmp_path / "plain"
    (space / ".datacore" / "events").mkdir(parents=True)
    log = EventLog(space, "own", sign=False)
    log.append("item.create", {"id": "t"})
    log.append("hub.enable", {"space": "x", "hub": "y", "epoch": 0, "grantor": "g"})
    assert H.authoritative_state(space).items["t"].status == "created"


# ── control records are never reversible (owner, round 7) ──────────────────

def test_r7_the_reversible_set_is_one_closed_allow_list():
    from ledger import finality
    assert isinstance(finality.REVERSIBLE_TYPES, frozenset)
    assert all(t.startswith("item.") for t in finality.REVERSIBLE_TYPES)
    for control in ("ledger.admit", "ledger.reject", "ledger.seal", "ledger.void", "ledger.compensate",
                    "hub.enable", "hub.start", "hub.promote", "hub.fence", "hub.resolve", "key.rotate",
                    "approval.grant", "owner.set", "item.grant", "item.lease", "item.lease.renew",
                    "item.lease.expire", "item.lease.conflict", "spend.record", "pool.budget",
                    "agent.checkin", "metric.attest", "artifact.attest", "policy.set", "item.verify"):
        assert control not in finality.REVERSIBLE_TYPES, control
        assert not finality.reversible(control), control
    assert finality.reversible("item.complete") and not finality.reversible("anything.new")
    # ONE list: no other module of the library keeps its own set of reversible types.
    for path in (LIB / "ledger").glob("*.py"):
        if path.name == "finality.py":
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    assert not (isinstance(tgt, ast.Name) and "REVERSIBLE" in tgt.id), (path.name, tgt.id)


def _admission_of(h, v):
    return next(e for e in h._state()[0] if e.type == "ledger.admit" and e.payload["event"]["hash"] == v.hash)


def _void_of(e, reason="the record was incorrect"):
    return {"log": getattr(e, "log", None) or e.actor, "seq": e.seq, "hash": e.hash, "reason": reason}


def test_r7_reversing_an_admission_record_is_refused_at_write(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    v, _ = owner.append("ledger.void", _void(done))
    admission = _admission_of(h, v)
    undo = T._signed_into(env, owner, "ledger.void", _void_of(admission))
    with pytest.raises(H.HubRefused) as exc:
        h.append(owner._submission(undo))
    assert exc.value.code == "not_reversible", exc.value
    h.seal()
    assert h._state()[1].items["t"].status == "claimed"            # the first reversal stands
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"
    assert owner.receipt_check(env.hub_dir, required=[undo.hash])["status"] != "confirmed"


def test_r7_the_writer_refuses_a_control_reversal_before_appending(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    v, _ = owner.append("ledger.void", _void(done))
    admission = _admission_of(h, v)
    T.git_pull(env.hub_dir, owner.space_dir)
    before = owner.log.path.read_text()
    with pytest.raises(H.HubRefused) as exc:
        owner.append("ledger.void", _void_of(admission))
    assert exc.value.code == "not_reversible"
    assert owner.log.path.read_text() == before                     # nothing signed into the log


def test_r7_a_control_reversal_imported_by_git_has_no_effect(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    v, _ = owner.append("ledger.void", _void(done))
    admission = _admission_of(h, v)
    undo = T._signed_into(env, owner, "ledger.void", _void_of(admission))
    T.git_pull(owner.space_dir, env.hub_dir)
    h.seal()
    assert undo.hash in _rejected_hashes(h)
    state = h._state()[1]
    assert not state.finality.voids.applies(admission.log, admission)
    assert state.items["t"].status == "claimed"
    assert state.outcomes[undo.hash].startswith("no-op")
    assert owner.receipt_check(env.hub_dir, required=[undo.hash])["status"] != "confirmed"


def test_r7_a_control_reversal_has_no_effect_even_if_a_grantor_admitted_it(env, monkeypatch):
    """Deterministic from the logs alone: an admission record (an older or
    faulty grantor's) cannot make a control record reversible."""
    from ledger import finality
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    rotation, _ = wa.append("key.rotate", {"actor": "wa", "reason": "review fixture"})
    real = finality.reversal_problem
    monkeypatch.setattr(finality, "reversal_problem",
                        lambda v, events, **kw: None if real(v, events, **kw) and
                        real(v, events, **kw)[0] == "not_reversible" else real(v, events, **kw))
    v, _ = owner.append("ledger.void", _void_of(rotation))
    assert v.hash in {(e.payload.get("event") or {}).get("hash") for e in h._state()[0] if e.type == "ledger.admit"}
    monkeypatch.setattr(finality, "reversal_problem", real)
    h.seal()
    state = h._state()[1]
    assert not state.finality.voids.applies("wa", rotation)
    assert state.outcomes[v.hash].startswith("no-op")
    replica = _replica(env, env.hub_dir)
    events, kw = H.authoritative_events(replica, registry_path=env.registry)
    from ledger.fold import fold
    assert fold(events, **kw).outcomes[v.hash].startswith("no-op")


def test_r7_reversing_a_key_rotation_is_refused_and_the_reader_keeps_it(env, monkeypatch):
    from ledger import keys
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    rotation, _ = wa.append("key.rotate", {"actor": "wa", "reason": "review fixture"})
    v = T._signed_into(env, owner, "ledger.void", _void_of(rotation))
    with pytest.raises(H.HubRefused) as exc:
        h.append(owner._submission(v))
    assert exc.value.code == "not_reversible"
    h.seal()
    assert wa.receipt_check(env.hub_dir, required=[rotation.hash])["status"] == "confirmed"
    assert rotation.hash in {e.hash for e in keys.recorded_rotations(env.hub_dir, "wa")}


def test_r7_reversing_an_approval_grant_is_refused(env, monkeypatch):
    from ledger import keys
    from ledger.policy import approval_payload_hash
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    approver = env.writer(T.APPROVER, H.LocalTransport(h))
    payload = {"id": "approved", "effects": ["publish"]}
    grant, _ = approver.append("approval.grant", {"item": "approved", "payload_hash": approval_payload_hash(payload)})
    v = T._signed_into(env, owner, "ledger.void", dict(_void_of(grant), log=T.APPROVER))
    with pytest.raises(H.HubRefused) as exc:
        h.append(owner._submission(v))
    assert exc.value.code == "not_reversible"
    T.git_pull(owner.space_dir, env.hub_dir)                        # and by git: still no effect
    h.seal()
    assert not h._state()[1].finality.voids.applies(T.APPROVER, grant)


def test_r7_keys_and_policy_readers_inherit_the_one_correction_rule(env, monkeypatch):
    """Guard: if the allow-list ever named a control type, the decision
    readers would still not see a reversed record -- they read through
    `finality.effective_events`, never around it."""
    from ledger import finality, keys
    from ledger.policy import Policy, PolicyError, approval_payload_hash, validate_approval
    from ledger.log import EventLog
    monkeypatch.setattr(finality, "REVERSIBLE_TYPES", finality.REVERSIBLE_TYPES | {"key.rotate", "approval.grant"})
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    rotation, _ = wa.append("key.rotate", {"actor": "wa", "reason": "review fixture"})
    owner.append("ledger.void", _void_of(rotation))
    approver = env.writer(T.APPROVER, H.LocalTransport(h))
    payload = {"id": "approved", "effects": ["publish"]}
    grant, _ = approver.append("approval.grant", {"item": "approved", "payload_hash": approval_payload_hash(payload)})
    owner.append("ledger.void", dict(_void_of(grant), log=T.APPROVER))
    h.seal()
    assert h._state()[1].finality.voids.applies("wa", rotation)
    assert rotation.hash not in {e.hash for e in keys.recorded_rotations(env.hub_dir, "wa")}
    log = EventLog(env.hub_dir, "wa", sign=True, keys_dir=env.keys_dir, registry_path=env.registry)
    with pytest.raises(PolicyError):
        validate_approval(log, dict(payload, approval_ref=grant.hash),
                          Policy(approver=T.APPROVER, cosign_effects={"publish"}))


def test_r7_an_item_event_is_still_reversible_inside_the_window(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    v, _ = owner.append("ledger.void", _void(done))
    h.seal()
    assert h._state()[1].items["t"].status == "claimed"
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"
