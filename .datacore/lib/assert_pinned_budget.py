#!/usr/bin/env python3
"""assert_pinned_budget.py — CI smoke assertion for the pinned engram budget.

Reads --json --pinned output from audit_engram_shape.py on stdin and
checks two invariants:

  1. fit_count > 0 — at least one pinned engram fits the budget (if every
     pinned engram is evicted, the session starts with no always-load rules)
  2. total_wire_cost within 20% of the stored baseline — guards against
     unnoticed accumulation that pushes the total cost far past the cap

Baseline lives at ~/.datacore/state/pinned-budget-baseline.json.
First run: writes the baseline and exits 0 (bootstrapping pass).
Subsequent runs: compare and update the baseline on success.

Usage:
  python3 .datacore/lib/audit_engram_shape.py --json --pinned | \\
    python3 .datacore/lib/assert_pinned_budget.py

Exit codes:
  0  all assertions pass
  1  one or more assertions failed
  2  input could not be parsed or --pinned key absent
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASELINE_PATH = Path.home() / ".datacore" / "state" / "pinned-budget-baseline.json"
DRIFT_TOLERANCE = 0.20  # 20 % window before flagging growth or shrinkage


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as e:
        print(f"FAIL: could not parse input JSON: {e}", file=sys.stderr)
        return 2

    pinned = payload.get("pinned")
    if pinned is None:
        print(
            "FAIL: JSON has no 'pinned' key — was --pinned passed to audit_engram_shape.py?",
            file=sys.stderr,
        )
        return 2

    fit_count: int = pinned.get("fit_count", 0)
    total_wire_cost: int = pinned.get("total_wire_cost", 0)
    budget: int = pinned.get("budget", 4000)
    pinned_count: int = pinned.get("pinned_count", 0)

    failures: list[str] = []

    # Assertion 1: at least one pinned engram must fit the session budget.
    if fit_count == 0:
        failures.append(
            f"fit_count == 0 — all {pinned_count} pinned engrams are evicted "
            f"(budget={budget} tokens, total_wire_cost={total_wire_cost}); "
            "no always-load rules reach the context window"
        )

    # Assertion 2: total_wire_cost must not diverge more than 20 % from baseline.
    baseline_data: dict = {}
    if BASELINE_PATH.exists():
        try:
            baseline_data = json.loads(BASELINE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            baseline_data = {}

    baseline_cost = baseline_data.get("total_wire_cost")

    if baseline_cost is None:
        # First run — establish baseline and pass (no prior reference to compare).
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(
            json.dumps({"total_wire_cost": total_wire_cost, "fit_count": fit_count}, indent=2)
        )
        if failures:
            for msg in failures:
                print(f"FAIL: {msg}", file=sys.stderr)
            return 1
        print(
            f"ok  pinned-budget: fit={fit_count}/{pinned_count}, "
            f"wire={total_wire_cost} (baseline established)"
        )
        return 0

    drift = abs(total_wire_cost - baseline_cost) / max(baseline_cost, 1)
    if drift > DRIFT_TOLERANCE:
        direction = "grew" if total_wire_cost > baseline_cost else "shrank"
        failures.append(
            f"total_wire_cost {direction} by {drift:.0%} "
            f"(current={total_wire_cost}, baseline={baseline_cost}, tolerance=20%)"
        )

    if failures:
        for msg in failures:
            print(f"FAIL: {msg}", file=sys.stderr)
        return 1

    # All assertions passed — update baseline for next run.
    BASELINE_PATH.write_text(
        json.dumps({"total_wire_cost": total_wire_cost, "fit_count": fit_count}, indent=2)
    )
    print(
        f"ok  pinned-budget: fit={fit_count}/{pinned_count}, "
        f"wire={total_wire_cost} (baseline={baseline_cost}, drift={drift:.1%})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
