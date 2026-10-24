"""LED-14: Every machine that holds the same history works out the same task
state and the same receipts.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-7 audit finding R10, present from the round-7
audited head (0758f855) to 8f98410f: every reader judged the history by the
policy file of the machine it ran on, so a machine whose policy file differed
(here: an arbitration order that forbids one agent dismissing another's
task) computed a different state and a "failed" receipt for an event the hub
had confirmed. Earlier rounds found the same class with the local principal
registry (round 5 C5) and the local clock (round 5 H1). (Formal counterpart:
`LED14_*` theorems in the Lean model, specs/datacore-lean.)

Promise, as evals: a hub space with an admitted dismissal of another agent's
task, a reversal inside the window, a rejected proposal and a handover to a
second hub; replicas rebuilt from the published logs -- in a different file
order, with different local policy files, and without the hub's private
state -- give the same state root, the same task states and the same receipt
for every event as the hub.
"""
from __future__ import annotations

import shutil

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _replica, env  # noqa: F401
from ledger import hub as H


def _local_policies():
    from ledger.hub_policy import PolicyInputs
    return {
        "same-as-hub": T.TEST_POLICY,
        "installation-file": None,
        "stricter-arbitration": PolicyInputs(approver=T.APPROVER, cosign_effects=T.TEST_POLICY.cosign_effects,
                                             arbitration=("wb", "wa")),
        "another-approver": PolicyInputs(approver="someone-else", cosign_effects=frozenset()),
    }


@pytest.fixture
def history(env, monkeypatch):
    T._with_principals(env, monkeypatch)
    h = env.hub()
    t = H.LocalTransport(h)
    own, wa, wb, owner = (env.writer(a, t) for a in ("own", "wa", "wb", T.OWNER))
    own.append("item.create", {"id": "t1", "assignee": "wb"})
    wb.append("item.claim", {"id": "t1"})
    dismissal, _ = wa.append("item.dismiss", {"id": "t1"})
    own.append("item.create", {"id": "t2", "assignee": "wa"})
    claim, _ = wa.append("item.claim", {"id": "t2"})
    v, _ = owner.append("ledger.void", {"log": "wa", "seq": claim.seq, "hash": claim.hash, "reason": "eval"})
    rejected = T._signed_into(env, wb, "item.claim", {"id": "t2"})
    h.append(wb._submission(rejected, rejected=True))
    h.seal()
    h.fence("eval handover")
    H.promote(env.hub_dir, "hubb", by=T.OWNER, reason="eval handover", keys_dir=env.keys_dir,
              registry_path=env.registry, clock=env.clock)
    h2 = env.hub("hubb")
    own.append("item.create", {"id": "t3"})
    h2.seal()
    return {"h": h2, "writers": {"own": own, "wa": wa, "wb": wb, T.OWNER: owner},
            "events": {"dismissal": dismissal, "claim": claim, "void": v, "rejected": rejected}}


def _view(state):
    return {iid: (it.status, it.owner) for iid, it in sorted(state.items.items())}


def _receipts(env, history, space, policy, tag):
    out = {}
    for name, e in sorted(history["events"].items()):
        w = history["writers"][e.actor]
        reader = H.HubWriter(w.space_dir, e.actor, H.LocalTransport(history["h"]), keys_dir=env.keys_dir,
                             registry_path=env.registry, state_dir=env.tmp / f"led14-{tag}-{name}",
                             clock=env.clock, policy=policy)
        out[name] = reader.receipt_check(space, required=[e.hash])["status"]
    return out


def test_every_replica_works_out_the_same_state_and_receipts(env, history):
    h = history["h"]
    hub_state = h._state()[1]
    hub_receipts = _receipts(env, history, env.hub_dir, T.TEST_POLICY, "hub")
    assert hub_receipts["dismissal"] == "confirmed" and hub_receipts["rejected"] != "confirmed", hub_receipts
    differences = []
    for pname, policy in _local_policies().items():
        replica = _replica(env, env.hub_dir)           # rebuilt line by line, files in reverse order
        state = H.authoritative_state(replica, registry_path=env.registry, policy=policy)
        if state.state_root() != hub_state.state_root() or _view(state) != _view(hub_state):
            differences.append(f"policy file '{pname}': state {_view(state)} != hub {_view(hub_state)}")
        got = _receipts(env, history, replica, policy, pname)
        if got != hub_receipts:
            differences.append(f"policy file '{pname}': receipts {got} != hub {hub_receipts}")
        shutil.rmtree(replica)
    assert not differences, "machines with the same history disagree: " + " | ".join(differences)
