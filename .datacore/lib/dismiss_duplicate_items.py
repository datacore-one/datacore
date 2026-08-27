#!/usr/bin/env python3
"""Dismiss ledger items that duplicate a task org already tracks under another id.

Created after an ID-churn repair on 2026-08-12. The sequence that produces
these is specific and worth naming, because it is easy to repeat:

  1. Something regenerates org `:ID:`s (`dedup_ids()` on load, then a save).
  2. `restore_ledger_ids.py` re-points org ids at their ledger items by heading.
  3. `ledger_ingest_org.py` then imports whatever it still cannot match as NEW.

Step 3 is correct in isolation — an org task with no ledger item SHOULD be
imported — but after step 2 it can mint a second item for a task that already
had one under the pre-churn id. The projection then shows `extra=N`: the ledger
holds more live items than org has tasks.

The dismissal is deliberately narrow. An item is only dismissed when ALL hold:

  * it is live (created/claimed/granted), so nothing already closed is touched
  * NO org `:ID:` anywhere in the space refers to it
  * its title matches a heading org still tracks — i.e. the work is genuinely
    represented, just under a different id

A live item whose title is absent from org is NOT a duplicate; it is a task
that vanished from org, which is a different incident and must stay visible.

`item.dismiss`, never `item.complete`: the fold requires status == claimed
before completing, so completing an unclaimed item is a silent no-op, and
fabricating a claim that never happened would put a lie in the audit trail.

    dismiss_duplicate_items.py [--root DIR] [--space NAME] [--apply]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.fold import fold  # noqa: E402
from ledger.log import EventLog, read_events  # noqa: E402

LIVE = ("created", "claimed", "granted")
ID_RE = re.compile(r":ID:\s*(\S+)")
HEADING_RE = re.compile(r"^\*+\s+(?:[A-Z]+\s+)?(?:\[#[A-Z]\]\s*)?(.*?)\s*(?::[\w:@#%]+:)?\s*$")


def org_state(space: Path) -> tuple[set[str], set[str]]:
    """(ids referenced by org, headings org still tracks)."""
    ids: set[str] = set()
    headings: set[str] = set()
    for name in ("next_actions.org", "inbox.org"):
        f = space / "org" / name
        if not f.exists():
            continue
        text = f.read_text(errors="replace")
        ids |= set(ID_RE.findall(text))
        for line in text.splitlines():
            if line.startswith("*"):
                m = HEADING_RE.match(line)
                if m and m.group(1).strip():
                    headings.add(m.group(1).strip())
    return ids, headings


def scan(space: Path) -> list[tuple[str, str]]:
    ids, headings = org_state(space)
    if not ids:
        return []
    state = fold(read_events(space))
    out = []
    for iid, item in state.items.items():
        if item.status not in LIVE or iid in ids:
            continue
        title = ((item.payload or {}).get("title") or "").strip()
        if title and title in headings:
            out.append((iid, title))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--space")
    ap.add_argument("--actor", default="mac")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    spaces = [s for s in sorted(a.root.glob("[0-9]-*"))
              if (s / "org").is_dir() and (not a.space or s.name == a.space)]
    if not spaces:
        print(f"ERROR: no spaces under {a.root} — refusing to report success")
        return 2

    total = 0
    for space in spaces:
        dupes = scan(space)
        total += len(dupes)
        print(f"{space.name:14} duplicate live items: {len(dupes)}")
        for iid, title in dupes[:3]:
            print(f"    {iid}  {title[:56]}")
        if dupes and a.apply:
            log = EventLog(space, a.actor)
            for iid, title in dupes:
                log.append("item.dismiss", {
                    "kind": "housekeeping",
                    "id": iid,
                    "reason": "duplicate: org tracks this task under another id "
                              "after an ID-churn repair",
                })

    print(f"\n{'dismissed' if a.apply else 'would dismiss'} {total} duplicate item(s)"
          + ("" if a.apply else " — re-run with --apply"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
