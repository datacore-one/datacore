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

from ledger.fold import fold  # noqa: E402
from ledger.genesis import import_space, scan  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402

ACTIVE = ("TODO", "NEXT", "WAITING", "DEFERRED", "QUEUED", "WORKING", "REVIEW", "FAILED")
LIVE = ("created", "claimed", "granted")


def sync_state(space: Path, actor: str = "mac", dry_run: bool = False) -> dict:
    """Reconcile an already-imported task with what org says about it NOW.

    Importing new tasks is a third of the job. Org keeps moving afterwards — a
    task is closed, rescheduled, or picked up as REVIEW by nightshift — and none
    of that reached the ledger, so the projection drifted from the file it is
    meant to reproduce. Two reconciliations, each a different event because they
    mean different things:

      CLOSED  org says DONE -> item.dismiss. NOT item.complete: the fold requires
              status == claimed before completing, so completing an unclaimed
              item is a SILENT no-op — two full passes over org-DONE tasks did
              nothing and reported success. Fabricating a claim that never
              happened to satisfy the state machine would put a lie in the audit
              trail. Dismiss is what "a human closed this" means.

      FIELDS  scheduled/deadline/state changed -> item.update carrying only the
              keys that actually differ.
    """
    from org_workspace import OrgWorkspace
    org_file = space / "org" / "next_actions.org"
    if not org_file.exists():
        return {"dismissed": 0, "updated": 0}
    ws = OrgWorkspace(); ws.load(str(org_file))
    state = fold(read_events(space))
    log = None
    dismissed = updated = 0
    for node in ws.all_nodes():
        nid = node.get_property("ID")
        if not nid:
            continue
        item = state.items.get(nid)
        if not item or item.status not in LIVE:
            continue
        if node.todo == "DONE":
            if not dry_run:
                log = log or EventLog(space, actor)
                log.append("item.dismiss", {"id": nid,
                           "reason": "closed as DONE in next_actions.org"})
            dismissed += 1
            continue
        if node.todo not in ACTIVE:
            continue
        cur = item.payload or {}
        # title too: a heading edited in org left the projection rendering the
        # imported wording forever, which is a diff no amount of state syncing
        # would ever close.
        # TAGS ARE DELIBERATELY NOT SYNCED, and this is the second time that
        # needs saying. `node.tags` from org_workspace is the INHERITED set —
        # the heading's own tags plus every ancestor's. The projector then files
        # the item under its own sections, which contribute their tags again, so
        # writing inherited tags into the payload double-applies inheritance and
        # the projection diverges from the file it is meant to reproduce.
        # Attempted 2026-08-12: 569 items updated, and 0-personal went from
        # clean to changed=46. Syncing tags at all needs the node's OWN tags,
        # which org_workspace does not expose separately here.
        want = {"state": node.todo,
                "title": node.heading,
                "scheduled": str(node.scheduled or "") or None,
                "deadline": str(node.deadline or "") or None}
        diff = {k: v for k, v in want.items() if (cur.get(k) or None) != v}
        if not diff:
            continue
        if not dry_run:
            log = log or EventLog(space, actor)
            log.append("item.update", {"id": nid, **diff})
        updated += 1
    return {"dismissed": dismissed, "updated": updated}

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


def _default_root() -> Path:
    """Root from DATACORE_ROOT, then ~/Data — NEVER from this file's location.

    Scheduled runs execute from a second checkout (~/.datacore/v2-runner) that
    holds no spaces. Derived from __file__, this swept zero spaces and printed
    "imported 0 task(s) across 0 space(s); 0 space(s) failed" — exit 0, contract
    green, and the org->ledger reconciliation silently not happening. Caught by
    running it in a cron-like environment instead of from a shell in ~/Data.
    """
    import os
    return Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=_default_root())
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    adapter = args.root / ".datacore" / "lib" / "org_workspace_adapter.py"
    spaces = sorted(p for p in args.root.glob("[0-9]-*") if (p / "org").is_dir())
    # Sweeping nothing is not a successful sweep.
    if not spaces:
        print(f"ERROR: no spaces with org/ under {args.root} — refusing to report success")
        return 2

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
            sy = sync_state(space, dry_run=args.dry_run)
            drift = new or sy["dismissed"] or sy["updated"]
            flag = "  <-- DRIFT" if drift else ""
            print(f"{space.name:14} new={new:4d} closed={sy['dismissed']:3d} "
                  f"updated={sy['updated']:3d} known={len(before.already_present):4d}{flag}")
        except Exception as exc:  # noqa: BLE001 - one bad space must not stop the sweep
            failures += 1
            print(f"{space.name:14} FAILED: {type(exc).__name__}: {exc}")

    verb = "would import" if args.dry_run else "imported"
    print(f"\n{verb} {total_new} task(s) across {len(spaces)} space(s); {failures} space(s) failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
