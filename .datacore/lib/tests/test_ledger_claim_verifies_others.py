"""Someone other than the doer checks the owner's requests (Winston path step 5, 2026-11-05).

Until now a delegated task was marked `completed` by the doer's own claim loop,
on the doer's machine, and nothing checked it again: an item ended "done" on the
doer's word. The ledger already holds the second step: `item.verify` from an
actor who is not the completer makes it `verified`.

The rule: with --verify-others, the claim tick re-runs the check of each
COMPLETED owner request (`asked_by`) that someone else completed, on this
machine, and records `item.verify` when it passes. A failing re-check is not a
verify. It never verifies its own work, and items nobody asked for are left
alone.

Kind: deterministic. The real dispatcher in a scratch fleet.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
# Delegates as winston and dispatches as miles: a declared roster, not the
# host's private registry (conftest.install_roster).
pytestmark = pytest.mark.usefixtures("install_roster")

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


@pytest.fixture
def fleet(monkeypatch):
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill, _install_local_executor
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    try:
        with scratch_fleet(prefix="verify-") as root:
            drill = DelegationDrill(root)
            _install_local_executor()       # the drill's executor stands in for the model
            yield drill.delegation_space("2-datacore"), drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def verify_tick(drill, space, actor):
    import io
    import contextlib
    import ledger_claim
    argv = sys.argv
    sys.argv = ["ledger_claim.py", "--space", str(space), "--actor", actor, "--verify-others"]
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            ledger_claim.main()
    finally:
        sys.argv = argv
    return buf.getvalue()


def test_a_completed_owner_request_is_verified_by_someone_else(fleet):
    space, drill = fleet
    iid = drill.delegate(space, by="winston", to="miles", title="write K into k.txt",
                         check="test -f k.txt", asked_by="gregor")
    drill.dispatch(space, "miles")
    assert drill.item(space, iid).status == "completed"
    out = verify_tick(drill, space, "winston")
    assert drill.item(space, iid).status == "verified", out[:400]


def test_a_failing_recheck_is_not_a_verify(fleet, tmp_path):
    space, drill = fleet
    gone = tmp_path / "flag"
    gone.write_text("x")
    iid = drill.delegate(space, by="winston", to="miles", title="write K into k.txt",
                         check=f"test -f k.txt && test -f {gone}", asked_by="gregor")
    drill.dispatch(space, "miles")
    assert drill.item(space, iid).status == "completed"
    gone.unlink()                                   # the result no longer holds
    out = verify_tick(drill, space, "winston")
    assert drill.item(space, iid).status == "completed", out[:400]
    assert "recheck failed" in out.lower()


def test_items_nobody_asked_for_are_left_alone(fleet):
    space, drill = fleet
    iid = drill.delegate(space, by="winston", to="miles", title="write K into k.txt",
                         check="test -f k.txt")
    drill.dispatch(space, "miles")
    verify_tick(drill, space, "winston")
    assert drill.item(space, iid).status == "completed"
