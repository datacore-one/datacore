#!/usr/bin/env python3
"""ARCHIVED 2026-08-26 — mission complete; triage markers confirmed in org files.

One-shot disposition of the 2026-07-25 QUEUED-zombie backlog.

Context: DIP-0009 v1.1 requeue coverage makes orphaned QUEUED tasks (outside
nightshift.org) requeue to NEXT — correct for the steady-state trickle, but
the accumulated backlog (~28 tasks, mostly April-vintage 1-datafund strategy
work) would have been reanimated INTO EXECUTION by the next overnight run.
Reanimating months-stale tasks unreviewed is a budget/trust decision, so this
script parks the existing backlog in WAITING with a triage marker instead.
The human promotes the survivors (WAITING → NEXT) via normal review; the
rest get CANCELLED. New orphans after today follow the spec path (→ NEXT).

Idempotent: only touches QUEUED tasks outside nightshift.org.

Usage: python3 zombie_disposition_2026_07_25.py ~/Data [--dry-run]
"""

import argparse
import sys
from pathlib import Path

from org_workspace import OrgWorkspace

MARKER = "zombie triage 2026-07-25 (state-vocabulary repair) — promote to NEXT or CANCEL"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data_dir", type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    parked = 0
    for org_file in sorted(args.data_dir.glob("[0-9]-*/org/*.org")):
        if "archive" in org_file.name.lower() or org_file.name == "nightshift.org":
            continue
        ws = OrgWorkspace()
        try:
            ws.load(org_file)
        except Exception as exc:  # noqa: BLE001 — skip unparseable, report
            print(f"SKIP {org_file}: {exc}", file=sys.stderr)
            continue
        dirty = False
        for node in list(ws.all_nodes()):
            if node.todo != "QUEUED":
                continue
            parked += 1
            rel = org_file.relative_to(args.data_dir)
            print(f"{'DRY ' if args.dry_run else ''}{rel}: {node.heading[:70]}")
            if not args.dry_run:
                ws.transition(node, "WAITING")
                ws.set_property(node, "WAITING_ON", MARKER)
                dirty = True
        if dirty:
            ws.save()
    print(f"{'Would park' if args.dry_run else 'Parked'}: {parked} zombies in WAITING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
