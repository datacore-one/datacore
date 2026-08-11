#!/usr/bin/env python3
"""Unresolved commit decisions, and how old they are (DIP-0046 E3).

The gate never blocks — nightshift runs ~20 tasks with nobody awake, and a gate
that waits for an answer turns one unreviewed commit into a stalled queue. The
cost of not blocking is that withheld work is invisible unless something counts
it, so this counts it.

Age is the signal, not volume. A handful of decisions from last night is the
gate working. The same handful still sitting there in a fortnight means the
operator is not resolving them, and a backlog nobody drains is a queue that has
quietly become a wastebasket — at which point the gate is discarding work while
appearing to protect it.

Resolution is deleting the file: review the withheld paths, commit or revert
them by hand, remove the record. No tooling has to exist for that to work.

Exit 0 when nothing is pending or everything is fresh, 1 when any decision is
older than --max-age-days (default 7), 2 on error.

    pending_decisions.py [--max-age-days N] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from commit_gate import pending  # noqa: E402


def age_days(stamp: str) -> float | None:
    for fmt in ("%Y%m%dT%H%M%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return (datetime.now() - datetime.strptime(stamp, fmt)).total_seconds() / 86400
        except (ValueError, TypeError):
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-age-days", type=float, default=7.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = pending()
    for r in rows:
        r["age_days"] = age_days(r.get("at") or "")
    # An unparseable stamp counts as STALE, never as fresh. A record whose age
    # cannot be established is exactly the one most likely to be forgotten.
    stale = [r for r in rows if r["age_days"] is None or r["age_days"] > args.max_age_days]

    if args.json:
        print(json.dumps({"pending": len(rows), "stale": len(stale),
                          "rows": rows}, indent=2, default=str))
    else:
        for r in rows:
            age = r["age_days"]
            tag = "STALE" if r in stale else "ok   "
            shown = f"{age:.1f}d" if age is not None else "age?"
            print(f"  {tag} {r.get('task_id','?'):<30} {shown:>6}  "
                  f"{len(r.get('withheld') or [])} withheld")
        print(f"\ncommit-decisions: {len(rows)} pending, {len(stale)} older than "
              f"{args.max_age_days:g}d")

    return 1 if stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
