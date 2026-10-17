"""mr-a-check: morning_repair.delegate() items carry a check so ledger_claim can close them.

Before this change, delegate() wrote repair items with no 'check', so the claim job's
only path was item.verify with needs_review=True, leaving the item claimed for ever
(103 such verifies, 87 stuck items, 2026-10-17). The fix attaches a check that runs
the same detector and passes when the finding is gone.

Tests (deterministic; no real system calls, no ledger events appended outside tmp):

  test_delegate_item_carries_a_check
    delegate() produces a payload with a 'check' field referencing the finding id.

  test_claim_completes_repair_item_when_check_passes
    When the check exits 0, the dispatcher completes the item (finding is gone).

  test_claim_leaves_item_open_when_check_fails
    When the check exits non-zero, the dispatcher releases the item and does not
    write item.verify -- the item re-enters the queue, not stuck claimed.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import morning_repair as M  # noqa: E402


# ── unit: delegate() writes the check into the payload ───────────────────────

def test_delegate_item_carries_a_check(monkeypatch, tmp_path):
    """The payload written by delegate() includes a 'check' field naming the finding id.

    Without this, ledger_claim has no criterion to pass and can only write
    item.verify with needs_review=True -- leaving the item permanently claimed.
    """
    import actor_identity
    import jobs.autofix as _af
    import ledger.policy

    captured = []
    monkeypatch.setattr(ledger.policy, "guarded_append",
                        lambda log, kind, payload: captured.append(payload))
    monkeypatch.setattr(M, "_repairer", lambda: "miles")
    monkeypatch.setattr(_af, "_space", lambda root: tmp_path)
    monkeypatch.setattr(actor_identity, "this_actor", lambda strict=False: "miles")

    finding = {"id": "v2-egress-declared", "kind": "v2",
               "title": "v2-verify: egress declared", "evidence": "2 undeclared"}
    iid = M.delegate(finding, "20261017")

    assert iid, "delegate() returned empty id -- item was not created"
    assert captured, "delegate() did not call guarded_append"
    payload = captured[0]
    assert "check" in payload, (
        "repair item created by delegate() has no 'check' field; "
        "ledger_claim can only write needs_review and leave it claimed for ever. "
        f"Keys present: {sorted(payload.keys())}")
    assert "v2-egress-declared" in payload["check"], (
        f"'check' does not reference the finding id 'v2-egress-declared': "
        f"{payload['check']!r}")


# ── integration: ledger_claim responds correctly to the check result ──────────

@pytest.fixture
def repair_fleet(monkeypatch):
    """A scratch fleet isolated from the real install; cleaned up after the test."""
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill, _install_local_executor
    from ledger_chaos_drill import scratch_fleet

    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH",
                        ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    os.environ["DATACORE_LEDGER_DRILL"] = "1"
    try:
        with scratch_fleet(prefix="mr-a-check-") as root:
            _install_local_executor()
            drill = DelegationDrill(root)
            space = drill.delegation_space("2-datacore")
            yield root, space, drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_claim_completes_repair_item_when_check_passes(repair_fleet, tmp_path):
    """A repair item whose check exits 0 (finding gone) is completed by the dispatcher."""
    root, space, drill = repair_fleet

    # The sentinel does NOT exist: finding is gone, check should pass.
    finding_sentinel = tmp_path / "finding-still-present"

    iid = drill.delegate(
        space,
        by="agent-b",
        to="agent-a",
        id="repair-v2-egress-declared-20261017",
        title="write DONE into result.txt",
        route="dev",
        morning_repair=True,
        check=f"test ! -f {finding_sentinel} && test -f result.txt",
    )

    out = drill.dispatch(space, "agent-a")
    item = drill.item(space, iid)

    assert item.status in ("completed", "verified", "done"), (
        f"repair item was not completed when check passed "
        f"(status={item.status!r}); dispatcher output: {out[:300]}")


def test_claim_leaves_item_open_when_check_fails(repair_fleet, tmp_path):
    """A repair item whose check exits non-zero (finding still present) is released,
    not stuck claimed, and no item.verify is written."""
    root, space, drill = repair_fleet

    # Sentinel EXISTS: finding is still present, check should fail.
    finding_sentinel = tmp_path / "finding-still-present"
    finding_sentinel.write_text("present\n")

    iid = drill.delegate(
        space,
        by="agent-b",
        to="agent-a",
        id="repair-v2-egress-declared-20261017",
        title="write DONE into result.txt",
        route="dev",
        morning_repair=True,
        check=f"test ! -f {finding_sentinel}",
    )

    out = drill.dispatch(space, "agent-a")
    item = drill.item(space, iid)

    assert item.status not in ("completed", "verified", "done"), (
        f"repair item was completed despite check failing -- finding was still present "
        f"(status={item.status!r}); dispatcher output: {out[:300]}")
    assert "REVIEW" not in out, (
        "dispatcher wrote item.verify (REVIEW) instead of running the check; "
        f"without a check the item stays claimed for ever. Output: {out[:300]}")
