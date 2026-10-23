"""LED-13: Records that grant authority (admissions, seals, keys, approvals,
ownership, leases, reviews) can never be reversed; a mistake in one is fixed
only by a new record.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-6 final and round-7 audit findings. On
7393dc67 the owner's reversal of an admission record, a key rotation or an
approval grant was admitted inside the window and changed what the readers
saw (final audit F3, F4, F5). On the round-7 audited head (0758f855) the hub
refused such a reversal, but the library writer and the command line still
signed it into the owner's log first (R4). (Formal counterpart: `LED13_*`
theorems in the Lean model, specs/datacore-lean.)

Promise, as evals: in a hub space holding one record of every authority
kind (activation, start, seal, admission, rejection, key rotation, approval,
ownership, lease, job attestation) and one ordinary task event --
  * the hub refuses a reversal of each authority record (`not_reversible`);
  * a writer that holds the record refuses before appending: the hub
    writer, the event log and the command line leave the log unchanged;
  * a reversal of each, imported by git -- even one a grantor's record
    admitted -- has no effect in the state on the hub or on a replica;
  * the ordinary task event is still reversible inside the window;
  * the reversible kinds are one closed list of task events, and a kind
    nobody has listed is not reversible.
"""
from __future__ import annotations

import argparse

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _replica, env  # noqa: F401
from ledger import hub as H
from ledger.log import EventLog, read_events

AUTHORITY_TYPES = ("hub.enable", "hub.start", "ledger.seal", "ledger.admit", "ledger.reject", "key.rotate",
                   "approval.grant", "owner.set", "item.lease", "metric.attest")


def _ref(e, reason="the record was wrong"):
    return {"log": getattr(e, "log", None) or e.actor, "seq": e.seq, "hash": e.hash, "reason": reason}


@pytest.fixture
def space(env, monkeypatch):
    T._with_principals(env, monkeypatch)
    h = env.hub()
    t = H.LocalTransport(h)
    own, wa, wb, owner, boss = (env.writer(a, t) for a in ("own", "wa", "wb", T.OWNER, T.APPROVER))
    own.append("item.create", {"id": "t", "assignee": "wa"})
    own.append("item.create", {"id": "leased", "assignee": "wb"})
    claim, _ = wa.append("item.claim", {"id": "t"})
    wb.request_lease("leased")
    wa.append("key.rotate", {"actor": "wa", "reason": "review fixture"})
    boss.append("approval.grant", {"item": "t", "payload_hash": "0" * 64})
    boss.append("owner.set", {"id": "t", "owner": "wa"})
    wa.append("metric.attest", {"metric": "job.verify", "job": "fixture", "ok": True, "failures": []})
    refused = T._signed_into(env, wb, "item.claim", {"id": "t"})
    h.append(wb._submission(refused, rejected=True))
    item_done, _ = wa.append("item.complete", {"id": "t"})
    v, _ = owner.append("ledger.void", _ref(item_done, "completion recorded on the wrong task"))
    h.seal()
    held = {}
    for e in h._state()[0]:
        if e.type in AUTHORITY_TYPES and e.type not in held:
            held[e.type] = e
    missing = sorted(set(AUTHORITY_TYPES) - set(held))
    assert not missing, f"fixture did not produce records of {missing}"
    T.git_pull(env.hub_dir, owner.space_dir)
    return {"h": h, "owner": owner, "held": held, "claim": claim}


def test_the_hub_refuses_reversing_any_authority_record(env, space):
    h, owner = space["h"], space["owner"]
    wrong = []
    for etype, rec in sorted(space["held"].items()):
        v = T._signed_into(env, owner, "ledger.void", _ref(rec))
        try:
            r = h.append(owner._submission(v))
            wrong.append(f"{etype}: accepted ({r.get('status')})")
        except H.HubRefused as exc:
            if exc.code != "not_reversible":
                wrong.append(f"{etype}: refused as {exc.code}, expected not_reversible")
        # whatever the door said, the record keeps its effect
        state = h._state()[1]
        if state.finality.voids.applies(getattr(rec, "log", None) or rec.actor, rec):
            wrong.append(f"{etype}: the reversal took effect")
    assert not wrong, "authority records were reversible: " + "; ".join(wrong)


@pytest.mark.parametrize("how", ["hub-writer", "event-log", "command-line"])
def test_a_writer_holding_the_record_refuses_before_appending(env, space, monkeypatch, how):
    owner = space["owner"]
    signed = []
    for etype, rec in sorted(space["held"].items()):
        before = owner.log.path.read_text()
        try:
            if how == "hub-writer":
                owner.append("ledger.void", _ref(rec))
            elif how == "event-log":
                EventLog(owner.space_dir, T.OWNER, sign=True, keys_dir=env.keys_dir,
                         registry_path=env.registry).append("ledger.void", _ref(rec))
            else:
                import ledger_cli
                monkeypatch.setattr(ledger_cli, "EventLog", lambda space, actor: EventLog(
                    space, actor, sign=True, keys_dir=env.keys_dir, registry_path=env.registry))
                ledger_cli.cmd_void(argparse.Namespace(space=str(owner.space_dir), actor=T.OWNER,
                                                       log=_ref(rec)["log"], seq=rec.seq, reason="wrong"))
        except (Exception, SystemExit):
            pass
        if owner.log.path.read_text() != before:
            signed.append(etype)
            owner.log.path.write_text(before)          # undo, so the next type is judged alone
    assert not signed, f"the {how} signed a reversal of these authority records into the log: {signed}"


def test_an_imported_reversal_has_no_effect_even_if_a_grantor_admitted_it(env, space):
    from ledger.finality import admit_payload
    from ledger.fold import fold
    h, owner = space["h"], space["owner"]
    voids = {}
    for etype, rec in sorted(space["held"].items()):
        v = T._signed_into(env, owner, "ledger.void", _ref(rec))
        voids[etype] = (v, rec)
    T.git_pull(owner.space_dir, env.hub_dir)
    for etype, (v, rec) in voids.items():              # a faulty grantor's admissions
        h.log.append("ledger.admit", admit_payload(v, T.OWNER, "reversal", admitted_ms=env.clock(),
                                                   target_admitted_ms=env.clock()))
    try:
        h.seal()
    except H.HubRefused:
        pass
    replica = _replica(env, env.hub_dir)
    events, kw = H.authoritative_events(replica, registry_path=env.registry, policy=T.TEST_POLICY)
    folds = {"hub": h._state()[1], "replica": fold(events, **kw)}
    took = [f"{where}: {etype}" for where, st in folds.items() for etype, (v, rec) in voids.items()
            if st.finality.voids.applies(getattr(rec, "log", None) or rec.actor, rec)]
    assert not took, f"imported reversals of authority records took effect: {took}"


def test_an_ordinary_task_event_is_still_reversible_inside_the_window(env, space):
    h, owner = space["h"], space["owner"]
    own = env.writer("own", H.LocalTransport(h))
    e, _ = own.append("item.create", {"id": "fresh", "title": "created by mistake"})
    v, _ = owner.append("ledger.void", _ref(e, "created by mistake"))
    h.seal()
    assert "fresh" not in h._state()[1].items, "a task event could not be reversed inside the window"
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"


def test_the_reversible_kinds_are_one_closed_list_of_task_events():
    from ledger import finality
    assert isinstance(finality.REVERSIBLE_TYPES, frozenset)
    assert all(t.startswith("item.") for t in finality.REVERSIBLE_TYPES), finality.REVERSIBLE_TYPES
    for t in AUTHORITY_TYPES + ("item.grant", "item.verify", "policy.set", "ledger.compensate", "ledger.void",
                                "hub.promote", "agent.checkin", "a.kind.added.later"):
        assert not finality.reversible(t), f"{t} must never be reversible"
