#!/usr/bin/env python3
"""Close ledger items that have no heading in ANY org file of their space.

These are items removed from org without a dismiss ever reaching the ledger
— deleted by hand, lost in a merge, or dropped by a tool that rewrote a
file. They stay live in the projection forever, so `all_clean` can never
become true and the Phase 1 gate stays shut.

WHY THIS IS SEPARATE FROM ledger_ingest_org's archived-dismiss. That one
fires on POSITIVE evidence — the id turned up in an archive file, so we know
where the heading went. This one fires on ABSENCE, which is weaker, so it is
a separate dry-run-by-default tool a human runs rather than part of the
hourly sweep.

NOT because org files can be caught half-written: org_workspace writes them
atomically (tmp in the same directory, fsync, os.replace), so a reader never
sees a partial file, and no truncation incident has ever been recorded. An
earlier version of this comment cited that hazard; it was invented, and the
guards below would have been justified on something that cannot happen.

The real ways a scan under-reports, each of which HAS happened here:
  - a bug in the scan itself — the first run of this tool found ZERO
    orphans while shadow_check reported fourteen, because `*.org` matched a
    vendored DIRECTORY (3-fds/…/vendor/golang.org) and because it counted
    .datacore/checkpoints, which is a rendering OF the ledger;
  - an unreadable path, which is what that directory raised;
  - a tree mid-merge, carrying conflict markers instead of headings —
    resolve_ledger_conflicts.py exists because that recurs.

Three guards make absence trustworthy enough to act on:

  WHOLE SPACE, NOT ONE FILE. Every `*.org` under the space is scanned —
  archives, nested project dirs, everything — so a task merely moved
  elsewhere is never mistaken for a deleted one.

  READABLE FILES ONLY. A space with any unreadable org file is skipped
  entirely. Not being able to read a file is not evidence the task is gone.

  A CEILING. If more than `--max-fraction` of a space's live items look
  orphaned, that is a broken scan, not a tidy corpus — refuse the whole
  space. Dismiss is terminal (DIP-0034); there is no undo to fall back on.
  This is not hypothetical: this tool shipped with two scan bugs, and the
  ceiling is what stands between the next one and a bulk close.

    python3 ledger_dismiss_orphans.py                 # report only
    python3 ledger_dismiss_orphans.py --execute
    python3 ledger_dismiss_orphans.py --space 5-plur --execute
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.fold import fold          # noqa: E402
from ledger.log import EventLog, read_events   # noqa: E402

LIVE = ("created", "claimed", "granted")
ID_RE = re.compile(r":ID:\s*(\S+)")


def _actor() -> str:
    return os.environ.get("DATACORE_ACTOR") or socket.gethostname().split(".")[0]


#: Generated org, which must NOT count as evidence org still has the task.
#: Both are renderings OF the ledger, so every live item appears in them by
#: construction — scanning them made every orphan look present and the tool
#: found nothing at all on its first real run.
DERIVED = (".datacore/checkpoints", ".datacore/state/projections")


def org_ids(space: Path) -> tuple[set[str], list[str]]:
    """Every id mentioned in every AUTHORED org file, plus unreadable ones."""
    ids: set[str] = set()
    unreadable: list[str] = []
    for f in sorted(space.rglob("*.org")):
        # `*.org` also matches DIRECTORIES — 3-fds vendors a Go tree containing
        # `vendor/golang.org/`, whose read raised IsADirectoryError and skipped
        # the entire space as "unreadable".
        if not f.is_file():
            continue
        rel = f.as_posix()
        if any(d in rel for d in DERIVED):
            continue
        try:
            ids |= set(ID_RE.findall(f.read_text(errors="replace")))
        except OSError as exc:
            unreadable.append(f"{f}: {exc}")
    return ids, unreadable


def orphans(space: Path) -> dict:
    present, unreadable = org_ids(space)
    if unreadable:
        return {"space": space.name, "skipped": "unreadable org file(s)",
                "detail": unreadable[:3], "orphans": []}

    state = fold(read_events(space))
    live = [i for i, it in state.items.items()
            if it.status in LIVE and not (it.payload or {}).get("section")]
    found = [i for i in live if i not in present]
    return {"space": space.name, "live": len(live), "orphans": sorted(found)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("DATACORE_ROOT")
                    or str(Path.home() / "Data"))
    ap.add_argument("--space", help="limit to one space")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--max-fraction", type=float, default=0.10,
                    help="refuse a space if more than this fraction look orphaned")
    a = ap.parse_args()
    root = Path(a.root).expanduser()

    spaces = ([root / a.space] if a.space
              else sorted(p for p in root.glob("[0-9]-*")
                          if (p / ".datacore" / "events").is_dir()))

    total = 0
    for space in spaces:
        if not (space / ".datacore" / "events").is_dir():
            continue
        r = orphans(space)
        if r.get("skipped"):
            print(f"{r['space']}: SKIPPED — {r['skipped']}: {r['detail']}")
            continue
        found, live = r["orphans"], r["live"]
        if not found:
            continue
        if live and len(found) / live > a.max_fraction:
            print(f"{r['space']}: REFUSED — {len(found)}/{live} live items look "
                  f"orphaned (> {a.max_fraction:.0%}). That is a broken scan, "
                  f"not a tidy corpus; dismiss is terminal, so nothing was touched.")
            continue

        log = None
        for iid in found:
            if a.execute:
                log = log or EventLog(space, _actor())
                log.append("item.dismiss", {
                    "id": iid, "kind": "housekeeping",
                    "reason": "no heading in any org file of this space"})
            print(f"{'' if a.execute else '[dry-run] '}{r['space']}: dismiss {iid}")
            total += 1

    print(f"\n{total} orphan(s)"
          + (" dismissed" if a.execute else " found (dry-run — nothing written)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
