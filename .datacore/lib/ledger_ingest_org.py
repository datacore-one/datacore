#!/usr/bin/env python3
"""Bring org-created tasks into the ledger. The other half of the migration.

`genesis.import_space()` was written as a one-shot migration and run once, so
the ledger holds a snapshot of the org files as they were on 2026-08-10. Every
task captured since -- by `/process-inbox`, by the GTD MCP tools, by hand --
exists only in org. The ledger drifts a little further from reality each day
and nothing reports it.

The importer is already idempotent: `scan()` folds the ledger first and only
returns items it has never seen, keyed on the org `:ID:`. So the fix is not new
import logic, it is RUNNING the existing logic on a schedule.

Two things this does that a bare import_space() loop would not:

  IDS FIRST. A heading with no `:ID:` is invisible to the ledger -- there is
  nothing stable to key on, so it can neither be imported nor deduped. Capture
  does not always assign one, so ensure-ids runs before every scan. Without
  this the sweep silently ignores exactly the newest tasks.

  DRIFT IS REPORTED, NOT JUST FIXED. The count of items that had gone missing
  is the interesting number: a sweep that quietly imports 40 tasks looks
  identical to one that imports 0, and the difference is whether capture is
  reaching the ledger at all.

Usage:
    ledger_ingest_org.py [--root DIR] [--dry-run]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.genesis import import_space, scan  # noqa: E402

ORG_FILES = ("inbox.org", "next_actions.org")


def ensure_ids(space: Path, adapter: Path) -> str:
    """Give every heading a stable :ID:. Returns a short status string."""
    touched = []
    for name in ORG_FILES:
        f = space / "org" / name
        if not f.exists():
            continue
        r = subprocess.run(
            [sys.executable, str(adapter), "ensure-ids", "--file", str(f)],
            capture_output=True, text=True, timeout=120,
        )
        touched.append(f"{name}:{'ok' if r.returncode == 0 else 'FAILED'}")
    return " ".join(touched) or "no org files"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    adapter = args.root / ".datacore" / "lib" / "org_workspace_adapter.py"
    spaces = sorted(p for p in args.root.glob("[0-9]-*") if (p / "org").is_dir())

    total_new = 0
    failures = 0
    for space in spaces:
        try:
            ids = "skipped (dry run)" if args.dry_run else ensure_ids(space, adapter)
            before = scan(space)
            new = len(before.importable)
            if new and not args.dry_run:
                import_space(space)
            total_new += new
            flag = "  <-- DRIFT" if new else ""
            print(f"{space.name:14} new={new:4d}  known={len(before.already_present):4d}  "
                  f"ids[{ids}]{flag}")
        except Exception as exc:  # noqa: BLE001 - one bad space must not stop the sweep
            failures += 1
            print(f"{space.name:14} FAILED: {type(exc).__name__}: {exc}")

    verb = "would import" if args.dry_run else "imported"
    print(f"\n{verb} {total_new} task(s) across {len(spaces)} space(s); {failures} space(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
