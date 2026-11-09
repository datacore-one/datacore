"""LED-10: Once the ledger has confirmed a task outcome, nothing that happens
later changes it: no late or tied arrival, import, restore, rebuild or hub
handover.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-7 audit findings. On the round-7 audited head
(0758f855) the late-event guard compared stamps with `<`, so a claim stamped
at EXACTLY the sealed claim's stamp took its place after the next seal (R1),
and a third hub after a middle hub that never sealed could seal without what
the first hub had sealed (R2). On 8f98410f the fold read the policy file as it
is now, so editing the policy turned a confirmed outcome into "failed" (R10).
Each variant below turns red on the head where it was found. (Formal
counterpart: `LED10_*` theorems in the Lean model, specs/datacore-lean.)

Promise, as evals: a task claimed by writer A is sealed (A's receipt
"confirmed"); then, on a fresh space per variant --
  * a rival claim stamped earlier, or with the same stamp, arrives over the
    network or by git;
  * the hub hands over to a second hub, then the rival claim arrives;
  * two handovers with no seal in the middle, the third hub starting from a
    copy that lacks A's log;
  * the hub restarts after losing its private record of what it judged;
  * a replica is rebuilt from the published logs alone;
  * the policy changes after the seal --
and after every variant and a new seal, A's receipt is still "confirmed", A
still owns the task, and the rival is never confirmed.
"""
from __future__ import annotations

import time

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _replica, env  # noqa: F401
from ledger import hub as H

VARIANTS = ["earlier-append", "earlier-git", "equal-append", "equal-git", "promoted-append", "promoted-git",
            "three-hubs", "restart-judgment-lost", "replica-rebuild"]


def _sealed_claim(env):
    h = env.hub()
    # A is "wb"; the rival "wa" sorts first, so a fold that orders equal
    # stamps by log name would put the rival ahead (round-7 audit, R1).
    own, wb, wa = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wb", "wa")]
    base = int(time.time() * 1000)
    h.append(own._submission(T._signed_into(env, own, "item.create", {"id": "t"}, hlc=f"{base:013d}.0000.own")))
    a = T._signed_into(env, wb, "item.claim", {"id": "t"}, hlc=f"{base + 200:013d}.0000.wb")
    h.append(wb._submission(a))
    h.seal()
    assert wb.receipt_check(env.hub_dir, required=[a.hash])["status"] == "confirmed"
    return h, wb, wa, a, base


def _deliver(env, h, rw, rival, how):
    if how == "git":
        T.git_pull(rw.space_dir, env.hub_dir)
        return
    try:
        h.append(rw._submission(rival))
    except H.HubRefused:
        pass                                  # refused at the door: fine, nothing changed


def _promote(env, h, to):
    h.fence("eval handover")
    H.promote(env.hub_dir, to, by=T.OWNER, reason="eval handover", keys_dir=env.keys_dir,
              registry_path=env.registry, clock=env.clock)
    return env.hub(to)


def _still_confirmed(env, space, h, wa, a, rival, variant):
    got = wa.receipt_check(space, required=[a.hash])["status"]
    assert got == "confirmed", f"expected A's sealed claim to stay confirmed after '{variant}'; it reads {got!r}"
    owner = h._state()[1].items["t"].owner
    assert owner == "wb", f"expected A to still own the task after '{variant}'; the owner is now {owner!r}"
    if rival is not None:
        r = wa.receipt_check(space, required=[rival.hash])["status"]
        assert r != "confirmed", f"the rival claim was confirmed after '{variant}'"


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_confirmed_claim_never_changes(env, variant):
    h, holder, other, a, base = _sealed_claim(env)
    space = env.hub_dir
    rival = None
    earlier = f"{base + 100:013d}.0000.wa"
    if variant.startswith(("earlier", "equal", "promoted")):
        if variant.startswith("promoted"):
            h = _promote(env, h, "hubb")
        # the very same stamp (writer suffix included), or an earlier one
        rival = T._signed_into(env, other, "item.claim", {"id": "t"},
                               hlc=a.hlc if variant.startswith("equal") else earlier)
        _deliver(env, h, other, rival, variant.split("-")[1])
        h.seal()
    elif variant == "three-hubs":
        h.fence("first handover")
        H.promote(env.hub_dir, "hubb", by=T.OWNER, reason="first", keys_dir=env.keys_dir,
                  registry_path=env.registry, clock=env.clock)
        middle = env.hub("hubb")
        middle.fence("second handover, no seal in between")
        H.promote(env.hub_dir, "hubc", by=T.OWNER, reason="second", keys_dir=env.keys_dir,
                  registry_path=env.registry, clock=env.clock)
        space = _replica(env, env.hub_dir)
        (space / ".datacore/events/wb.jsonl").unlink()
        h = env.hub("hubc", space=space, state="third")
        rival = T._signed_into(env, other, "item.claim", {"id": "t"}, hlc=earlier)
        _deliver(env, h, other, rival, "append")
        try:
            h.seal()                           # may refuse while it lacks the sealed history
        except H.HubRefused:
            pass
        T.git_pull(holder.space_dir, space)    # the original log comes back
        h.seal()
    elif variant == "restart-judgment-lost":
        h.close()
        for f in (env.tmp / "state" / T.GRANTOR).glob("*.judged.json"):
            f.unlink()
        h = env.hub()
        rival = T._signed_into(env, other, "item.claim", {"id": "t"}, hlc=earlier)
        _deliver(env, h, other, rival, "git")
        h.seal()
    elif variant == "replica-rebuild":
        space = _replica(env, env.hub_dir)
        state = H.authoritative_state(space, registry_path=env.registry, policy=T.TEST_POLICY)
        assert state.items["t"].owner == "wb", "a replica rebuilt from the logs gives the task to someone else"
    _still_confirmed(env, space, h, holder, a, rival, variant)


def test_a_policy_change_after_the_seal_changes_no_confirmed_outcome(env):
    """The owner's later policy (arbitration that would forbid it) never
    reverses wa's sealed dismissal of wb's task."""
    from ledger.hub_policy import PolicyInputs
    h = env.hub()
    own, wa, wb = [env.writer(a, H.LocalTransport(h)) for a in ("own", "wa", "wb")]
    own.append("item.create", {"id": "t", "assignee": "wb"})
    wb.append("item.claim", {"id": "t"})
    dismissal, _ = wa.append("item.dismiss", {"id": "t"})
    h.seal()
    assert wa.receipt_check(env.hub_dir, required=[dismissal.hash])["status"] == "confirmed"
    h.close()
    stricter = PolicyInputs(approver=T.APPROVER, cosign_effects=T.TEST_POLICY.cosign_effects,
                            arbitration=("wb", "wa"))
    h = env.hub(policy=stricter, state="after-policy-change")
    h.seal()
    reader = H.HubWriter(wa.space_dir, "wa", H.LocalTransport(h), keys_dir=env.keys_dir,
                         registry_path=env.registry, state_dir=env.tmp / "led10-reader", clock=env.clock,
                         policy=stricter)
    got = reader.receipt_check(env.hub_dir, required=[dismissal.hash])["status"]
    assert got == "confirmed", f"expected the sealed dismissal to stay confirmed after a policy change; got {got!r}"
    assert h._state()[1].items["t"].status == "dismissed"
