"""LED-15: A mistaken task change can be reversed only by the space's owner,
within one hour of the hub accepting it and before it is sealed; after that
only a new correcting record fixes it.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-6 audit findings on its audited head
(d3778523): a reversal whose body pin named other content was refused by
nothing and got a confirmed receipt (N1), and a hub restarted with a stale
admission record gave an old event a fresh admission time, reopening its
window (N2). Earlier rounds: the writer's own timestamp decided the window
(round 5 H1) and the local principal registry decided who may reverse
(round 5 C5). (Formal counterpart: `LED15_*` theorems in the Lean model,
specs/datacore-lean.)

Promise, as evals: for one completed task event, only the owner's reversal
inside the hour by the hub's clock and before the seal takes effect. Every
other reversal -- at 61 minutes, by a human who is not the owner, by the
event's own writer, after the seal, with a timestamp chosen to look early,
with a body pin of other content, after a restart from a stale admission
record -- is refused at the door and, imported by git, has no effect; and a
later change to the principal registry changes no decision.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _confirmed, _replica, _void, env  # noqa: F401
from ledger import hub as H

HOUR_S = 60 * 60
REFUSED = ["after-61-minutes", "non-owner-human", "own-writer", "after-seal", "backdated-stamp", "wrong-body-pin",
           "stale-admission-record"]


def _status(h, env):
    return h._state()[1].items["t"].status


def test_the_owner_reverses_inside_the_hour(env, monkeypatch):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    env.clock.advance(HOUR_S - 60)                          # 59 minutes by the hub's clock
    v, answer = owner.append("ledger.void", _void(done))
    assert answer["status"] == "accepted", answer
    h.seal()
    assert _status(h, env) == "claimed", "the owner's reversal inside the hour did not take effect"
    assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"


@pytest.mark.parametrize("variant", REFUSED)
def test_every_other_reversal_is_refused_and_has_no_effect(env, monkeypatch, variant):
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    by, payload, hlc = owner, _void(done), None
    if variant == "after-61-minutes":
        env.clock.advance(HOUR_S + 60)
    elif variant == "non-owner-human":
        by = own                                            # a human principal of the registry, not the owner
    elif variant == "own-writer":
        by = wa
    elif variant == "after-seal":
        h.seal()
    elif variant == "backdated-stamp":
        env.clock.advance(HOUR_S + 60)
        hlc = f"{int(done.hlc.split('.')[0]) + 1:013d}.0000.{T.OWNER}"
    elif variant == "wrong-body-pin":
        payload = dict(payload, body_sha256="0" * 64)
    elif variant == "stale-admission-record":
        data = json.loads(h._admitted_path.read_text())
        data["seen"]["wa"] = done.seq - 1                   # a valid copy from before the completion
        data["times"].get("wa", {}).pop(str(done.seq), None)
        h.close()
        h._admitted_path.write_text(json.dumps(data))
        env.clock.advance(HOUR_S + 60)
        h = env.hub()
    v = T._signed_into(env, by, "ledger.void", payload, hlc=hlc)
    try:
        r = h.append(by._submission(v))
        pytest.fail(f"[{variant}] the hub accepted the reversal ({r.get('status')})")
    except H.HubRefused:
        pass
    T.git_pull(by.space_dir, env.hub_dir)                   # and by git: judged, never effective
    h.seal()
    assert _status(h, env) == "completed", f"[{variant}] the reversal took effect"
    assert by.receipt_check(env.hub_dir, required=[v.hash])["status"] != "confirmed", \
        f"[{variant}] the refused reversal got a confirmed receipt"
    replica = _replica(env, env.hub_dir)
    st = H.authoritative_state(replica, registry_path=env.registry, policy=T.TEST_POLICY)
    assert st.items["t"].status == "completed", f"[{variant}] a replica applies the reversal"


def test_a_registry_change_changes_no_reversal_decision(env, monkeypatch):
    import actor_identity
    h, own, wa, owner, done = _confirmed(env, monkeypatch)
    v, _ = owner.append("ledger.void", _void(done))
    h.seal()
    registry = Path(actor_identity.PRINCIPALS)
    for text in (registry.read_text().replace("owner: {kind: human}", "owner: {kind: agent}"), "principals: {}\n"):
        registry.write_text(text)
        assert _status(h, env) == "claimed", "a registry change undid the owner's reversal"
        assert owner.receipt_check(env.hub_dir, required=[v.hash])["status"] == "confirmed"
