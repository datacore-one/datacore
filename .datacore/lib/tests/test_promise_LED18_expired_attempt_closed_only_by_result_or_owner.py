"""LED-18: An expired work attempt counts as closed only when its holder's
result stands or the owner closes it.

Kind: deterministic (temporary hub spaces; never the live installation).

Seeded failure: the real round-7 audit finding R7, on the round-7 audited
head (0758f855): the holder released the task, the owner reversed that
release inside the window, the lease then expired -- and the hub still
counted the reversed release as closing the attempt, so the task could be
handed to someone else while the first holder might still be working.
(No Lean theorem covers this promise yet.)

Promise, as evals: a task leased to its holder, whose lease then expires --
  * a release the owner reversed inside the window does not close the attempt;
  * a release naming another attempt or other task content does not close it;
  * the holder's own standing release closes it;
  * the owner's resolution closes it.
"""
from __future__ import annotations

import pytest

from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import _setup, _void, env  # noqa: F401
from ledger import hub as H

CASES = {"reversed-release": False, "other-attempt": False, "other-content": False,
         "standing-release": True, "owner-resolution": True}


@pytest.mark.parametrize("case", sorted(CASES))
def test_an_expired_attempt_is_closed_only_by_a_standing_result_or_the_owner(env, case):
    h, own, wa, grant = _setup(env)
    attempt, task_hash = grant["attempt"], grant["task_hash"]
    if case in ("reversed-release", "standing-release"):
        release, _ = wa.append("item.release", {"id": "t", "attempt": attempt, "task_hash": task_hash})
        if case == "reversed-release":
            owner = env.writer(T.OWNER, H.LocalTransport(h))
            owner.append("ledger.void", _void(release))
    elif case in ("other-attempt", "other-content"):
        wrong = {"attempt": "not-this-attempt"} if case == "other-attempt" else {"task_hash": "0" * 64}
        e = T._signed_into(env, wa, "item.release", dict({"id": "t", "attempt": attempt, "task_hash": task_hash},
                                                         **wrong))
        try:
            h.append(wa._submission(e))
        except H.HubRefused:
            pass
    elif case == "owner-resolution":
        H.resolve_attempt(env.hub_dir, "t", attempt, by=T.OWNER, reason="the holder never came back",
                          keys_dir=env.keys_dir, registry_path=env.registry)
    env.clock.advance(901)                                   # past the lease
    h.expire_due()
    events, state = h._state()
    closed = h._attempt_resolved(state.items["t"], events, "wa", state)
    expected = CASES[case]
    assert closed == expected, (f"[{case}] expected the expired attempt to be "
                                f"{'closed' if expected else 'still open (uncertain)'}; it is "
                                f"{'closed' if closed else 'open'}")
