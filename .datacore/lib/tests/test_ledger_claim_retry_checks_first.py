"""A retry runs the item's check before the agent: work already done is not done again.

Defect this pins (2026-10-29): Data's task was "post P7 to X, then record the
URL". The post went out; the done-check then failed for a reason of its own (it
did not count a bare domain as a link). `ledger_claim` released the item and
the next tick ran the agent again, which posted again: P7 went out three
times. A post cannot be undone, so a failed check must not mean "do the work
again" when the work's effect is already there.

The rule: on a retry (the item was released before), run the check first. It
passes -> complete on that evidence, agent not run. It fails -> an ordinary
attempt, as before.

Kind: deterministic. The real dispatcher in a scratch fleet; the drill's local
executor stands in for the model, and `run_task` is counted.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


@pytest.fixture
def fleet(monkeypatch):
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    try:
        with scratch_fleet(prefix="retry-") as root:
            drill = DelegationDrill(root)
            yield drill.delegation_space("2-datacore"), drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


@pytest.fixture
def agent_runs(monkeypatch):
    import ledger_claim
    runs = []
    real = ledger_claim.run_task

    def counted(*a, **k):
        runs.append(a[0])
        return real(*a, **k)
    monkeypatch.setattr(ledger_claim, "run_task", counted)
    return runs


def test_a_retry_whose_check_now_passes_completes_without_the_agent(fleet, agent_runs, tmp_path):
    space, drill = fleet
    fixed = tmp_path / "check-fixed"
    iid = drill.delegate(space, by="winston", to="miles", title="write K into k.txt",
                         check=f"test -f k.txt && test -f {fixed}")
    drill.dispatch(space, "miles")
    assert len(agent_runs) == 1
    assert drill.item(space, iid).status != "dismissed"

    fixed.write_text("the check was wrong, the work was right\n")
    out = drill.dispatch(space, "miles")
    it = drill.item(space, iid)
    assert it.status in ("done", "completed") or it.closed_kind == "done", (
        f"the retry did not complete on the passing check: {it.status}; {out[:300]}")
    assert len(agent_runs) == 1, (
        f"the agent was run again on work already done ({len(agent_runs)} runs): {out[:300]}")


def test_a_retry_whose_check_still_fails_runs_the_agent(fleet, agent_runs):
    """Control: checking first is not a shortcut past the work."""
    from ledger_claim import MAX_ATTEMPTS
    space, drill = fleet
    iid = drill.delegate(space, by="winston", to="miles", title="write K into k.txt",
                         check="test -f k.txt && exit 1")
    outs = [drill.dispatch(space, "miles") for _ in range(MAX_ATTEMPTS + 1)]
    assert len(agent_runs) == MAX_ATTEMPTS
    assert drill.item(space, iid).status == "dismissed", outs[-1][:300]
