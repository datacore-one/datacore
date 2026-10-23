"""LED-11: Every part of Datacore that decides or shows anything from a hub
space sees only what the hub admitted.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-7 audit finding R8. On the round-7 audited head
(0758f855) the generic verifier's correction reader looked for the activation
record with a raw byte search; a JSON-escaped `hub.enable` in a space that had
lost its hub config sent it back to the old registry rule, so an unadmitted,
unsigned reversal took effect there while every other reader ignored it.
Earlier rounds found the same shape in the seal-status reader (round 4), the
job-health reader (round 5 C6, round 6 N4) and the key and approval readers
(round-6 final F4, F5). (Formal counterpart: `LED11_*` theorems in the Lean
model, specs/datacore-lean.)

Promise, as evals: a hub space holds, imported by git,
  (i)   an unsigned claim by a writer the task was never given to,
  (ii)  a signed claim the hub refused and keeps as a rejected proposal,
  (iii) an item event the owner reversed inside the correction window,
  (iv)  unsigned control records: a key rotation, an approval grant, a job
        attestation and a reversal,
  (v)   a signed reversal the hub refused (the window had closed),
and every decision reader -- the state, the settled state, the seal check,
receipts, generic verification's correction reader, the key-rotation reader,
approval validation and the job-health reader -- ignores all of them; the
correction reader does so also when the config is lost and the activation
record is JSON-escaped. The code-shape guard confirms that no module in the
decision gates' import closure reads raw events around admission.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _replica, env  # noqa: F401
from ledger import hub as H
from ledger.events import to_line
from ledger.log import read_events


def _forge(env, writer, log: str, type: str, payload: dict):
    """An unsigned copy of a well-formed event, alone in its own log file, imported by git."""
    fake = replace(T._signed_into(env, writer, type, payload), sig="")
    lines = writer.log.path.read_text().splitlines()
    writer.log.path.write_text("\n".join(lines[:-1]) + ("\n" if lines[:-1] else ""))
    path = env.hub_dir / ".datacore" / "events" / f"{log}.jsonl"
    path.write_text(to_line(fake) + "\n")
    return fake


@pytest.fixture
def space(env, monkeypatch):
    import ledger.keys as keys
    T._with_principals(env, monkeypatch)
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)
    h = env.hub()
    t = H.LocalTransport(h)
    own, wa, wb, owner = (env.writer(a, t) for a in ("own", "wa", "wb", T.OWNER))
    boss = env.writer(T.APPROVER, t)
    for i in (1, 2, 3, 4):
        own.append("item.create", {"id": f"t{i}", "assignee": "wa"})
    forged = {}
    # (i) an unsigned claim by wb, who was never given t1
    forged["claim"] = _forge(env, wb, "wb-forged", "item.claim", {"id": "t1"})
    # (ii) a signed claim of t2 by wb: refused, kept as a rejected proposal
    rejected = T._signed_into(env, wb, "item.claim", {"id": "t2"})
    r = h.append(wb._submission(rejected, rejected=True))
    assert r["status"] == "rejected", r
    # (iii) wa's claim of t3, reversed by the owner inside the window
    claim3, _ = wa.append("item.claim", {"id": "t3"})
    owner.append("ledger.void", {"log": "wa", "seq": claim3.seq, "hash": claim3.hash, "reason": "eval"})
    # (iv) unsigned control records
    claim4, _ = wa.append("item.claim", {"id": "t4"})
    forged["rotation"] = _forge(env, wa, "wa-forged-rotation", "key.rotate", {"actor": "wa", "reason": "forged"})
    from ledger.policy import approval_payload_hash
    approved = {"id": "approved", "effects": ["payment"]}
    forged["grant"] = _forge(env, boss, "boss-forged", "approval.grant",
                             {"item": "approved", "payload_hash": approval_payload_hash(approved)})
    forged["attest"] = _forge(env, wa, "wa-forged-attest", "metric.attest",
                              {"metric": "job.verify", "job": "critical-job", "ok": True, "failures": []})
    forged["void"] = _forge(env, owner, "owner-forged", "ledger.void",
                            {"log": "wa", "seq": claim4.seq, "hash": claim4.hash, "reason": "forged"})
    h.seal()
    # (v) a signed reversal of the now sealed claim of t4: the hub refuses it
    # (closed window) and keeps it as a rejected proposal
    late_void = T._signed_into(env, owner, "ledger.void",
                               {"log": "wa", "seq": claim4.seq, "hash": claim4.hash, "reason": "after the seal"})
    r = h.append(owner._submission(late_void, rejected=True))
    assert r["status"] == "rejected", r
    h.seal()
    return {"h": h, "wa": wa, "wb": wb, "rejected": rejected, "claim3": claim3, "claim4": claim4,
            "approved": approved, "forged": forged}


def _assert_state(state, where):
    assert state.items["t1"].owner != "wb", f"{where}: shows the unsigned claim (t1 owned by wb)"
    assert state.items["t2"].owner != "wb", f"{where}: shows the rejected proposal (t2 owned by wb)"
    assert state.items["t3"].owner != "wa", f"{where}: shows a reversed claim (t3 still owned by wa)"
    assert state.items["t4"].owner == "wa", f"{where}: an unsigned reversal took wa's claim of t4 away"


def test_the_state_readers_see_only_what_the_hub_admitted(env, space):
    _assert_state(space["h"]._state()[1], "the hub's state")
    for name, s in (("hub space", env.hub_dir), ("replica", _replica(env, env.hub_dir))):
        _assert_state(H.authoritative_state(s, registry_path=env.registry, policy=T.TEST_POLICY), f"{name} state")
        _assert_state(H.authoritative_state(s, registry_path=env.registry, policy=T.TEST_POLICY, settled=True),
                      f"{name} settled state")
        ok, detail = H.seal_status(s, registry_path=env.registry, policy=T.TEST_POLICY)
        assert ok is True, f"{name}: the seal check disagrees with the admitted state: {detail}"


def test_receipts_never_confirm_what_the_hub_did_not_admit(env, space):
    wa, wb = space["wa"], space["wb"]
    for e, who in ((space["rejected"], wb), (space["claim3"], wa)):
        got = who.receipt_check(env.hub_dir, required=[e.hash])["status"]
        assert got != "confirmed", f"a receipt confirmed {e.type} {e.hash[:12]} the hub did not let stand"
    assert wa.receipt_check(env.hub_dir, required=[space["claim4"].hash])["status"] == "confirmed"


def test_the_correction_reader_honours_no_unadmitted_reversal(env, space):
    from ledger import voids
    got = voids.for_events_dir(env.hub_dir / ".datacore" / "events", registry_path=env.registry)
    assert not got.applies("wa", space["claim4"]), "generic verification honours an unadmitted reversal"


def test_the_correction_reader_stays_closed_when_the_config_is_lost_and_the_record_is_escaped(env, space):
    from ledger import voids
    replica = _replica(env, env.hub_dir)
    (replica / H.CONFIG).unlink()
    owner_path = replica / ".datacore/events" / f"{T.OWNER}.jsonl"
    owner_path.write_text(owner_path.read_text().replace('"hub.enable"', '"hub.\\u0065nable"'))
    try:
        got = voids.for_events_dir(replica / ".datacore/events", registry_path=env.registry)
    except H.HubUnavailable:
        return                                  # refusing to read is fine; reading around admission is not
    assert not got.applies("wa", space["claim4"]), (
        "with the config lost and the activation record escaped, generic verification honours a "
        "reversal the hub refused, by the old registry rule")


def test_the_key_approval_and_job_readers_ignore_unadmitted_records(env, space):
    import actor_identity
    import job_attestations
    from ledger import keys
    from ledger.log import EventLog
    from ledger.policy import Policy, PolicyError, validate_approval
    f = space["forged"]
    assert f["rotation"].hash not in {e.hash for e in keys.recorded_rotations(env.hub_dir, "wa")}, \
        "the key-rotation reader counts an unsigned rotation"
    log = EventLog(env.hub_dir, "wa", sign=True, keys_dir=env.keys_dir, registry_path=env.registry)
    with pytest.raises(PolicyError):
        validate_approval(log, dict(space["approved"], approval_ref=f["grant"].hash),
                          Policy(approver=T.APPROVER, cosign_effects={"payment"}))
    got = job_attestations.latest_jobs(env.hub_dir, (T._real_ms() + 60_000) / 1000,
                                      registry=Path(actor_identity.PRINCIPALS))
    assert "critical-job" not in got.get("wa", {}), "the job-health reader counts an unsigned attestation"


def test_no_decision_reader_reads_raw_events_around_admission():
    from tests import test_ledger_hub_round6 as R6
    offenders = [hit for mod, p in R6._decision_closure(R6._LIB).items()
                 if str(p.relative_to(R6._LIB)) not in R6._RAW_READ_ALLOWED for hit in R6._raw_reads(p, R6._LIB)]
    assert offenders == [], f"decision readers that read raw events around admission: {offenders}"
