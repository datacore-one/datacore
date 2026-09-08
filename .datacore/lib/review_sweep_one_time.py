#!/usr/bin/env python3
"""
review_sweep_one_time.py — one-time backlog sweep for orphaned Review: tasks.

Targets Review: tasks that have no machine-readable artifact signal (no PR_URL,
no NIGHTSHIFT_OUTPUT) and are past their scheduled date by a configurable
number of days. These tasks predate the NIGHTSHIFT_OUTPUT tracking property
and cannot be auto-closed by gh_reconcile.py's normal path.

Close condition: TODO/NEXT Review: task with
  - no NIGHTSHIFT_OUTPUT property (predates tracking)
  - no GitHub ref in heading or tracking properties
  - scheduled date more than DAYS_STALE days ago

Marks DONE with RESULT="one-time sweep: scheduled >N days ago, no open artifact ref"
and NIGHTSHIFT_RECONCILED timestamp.

Usage:
    python3 review_sweep_one_time.py [--dry-run] [--days-stale 60] [--verbose]
    python3 review_sweep_one_time.py --dry-run          # see what would close
    python3 review_sweep_one_time.py                    # apply changes
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from datetime import date
from pathlib import Path
from typing import List, Optional, Dict, Tuple

log = logging.getLogger("review_sweep")

OPEN_STATES = frozenset({"TODO", "NEXT", "WAITING", "QUEUED"})

# Properties with GitHub refs that override the "no-ref" assumption
TRACKING_PROPS = frozenset({
    "PR", "LINK", "ISSUE", "GITHUB_ISSUE", "GITHUB_PR", "GITHUB_URL",
    "EPIC", "BLOCKER", "MERGE", "PR_URL", "ISSUE_URL", "NIGHTSHIFT_OUTPUT",
})

RE_HEADING = re.compile(
    r"^(\*+)\s+"
    r"(TODO|NEXT|WAITING|QUEUED|WORKING|DONE|REVIEW|FAILED|CANCELLED|DEFERRED|EXECUTING)"
    r"(\s)"
)
RE_GH_URL = re.compile(r"https://github\.com/")
RE_GH_BARE = re.compile(r"\b(PR|issue|pull)\s*#\d+\b", re.IGNORECASE)
RE_SCHED = re.compile(r"^\s*SCHEDULED:\s*<(\d{4}-\d{2}-\d{2})", re.MULTILINE)


def today_org_ts() -> str:
    d = date.today()
    return f"[{d.strftime('%Y-%m-%d')} {d.strftime('%a')}]"


def _has_gh_ref(heading: str, props: Dict[str, str], body_lines: List[str]) -> bool:
    """True if the task has any GitHub reference (URL or bare ref)."""
    # Full GitHub URLs anywhere
    all_text = heading + "\n" + "\n".join(body_lines)
    for v in props.values():
        all_text += "\n" + v
    if RE_GH_URL.search(all_text):
        return True
    # Bare PR/issue refs in heading or tracking props only
    if RE_GH_BARE.search(heading):
        return True
    for k, v in props.items():
        if k.upper() in TRACKING_PROPS and RE_GH_BARE.search(v):
            return True
    return False


def parse_and_sweep(
    org_file: Path,
    days_stale: int,
    dry_run: bool,
    today: date,
) -> Tuple[int, List[dict]]:
    """
    Sweep one file. Returns (count_closed, list_of_closed_task_info).
    Modifies file in-place if dry_run is False.
    """
    content = org_file.read_text(encoding="utf-8")
    lines = content.split("\n")
    cutoff = date.fromordinal(today.toordinal() - days_stale)

    closed: List[dict] = []
    to_close: List[dict] = []  # {heading_line, heading, reason}

    i = 0
    while i < len(lines):
        hm = RE_HEADING.match(lines[i])
        if not hm:
            i += 1
            continue

        state = hm.group(2)
        if state not in OPEN_STATES:
            i += 1
            continue

        rest = lines[i][hm.end():]
        # Must start with "Review: "
        if not rest.strip().startswith("Review:"):
            i += 1
            continue

        # Strip tags from heading
        tag_match = re.search(r"\s+(:[:\w]+:)\s*$", rest)
        if tag_match:
            heading = rest[:tag_match.start()].strip()
        else:
            heading = rest.strip()

        # Skip SCHEDULED/DEADLINE lines
        j = i + 1
        scheduled_date: Optional[date] = None
        while j < len(lines) and re.match(r"^\s*(SCHEDULED|DEADLINE|CLOSED):", lines[j]):
            m = re.match(r"^\s*SCHEDULED:\s*<(\d{4}-\d{2}-\d{2})", lines[j])
            if m:
                try:
                    scheduled_date = date.fromisoformat(m.group(1))
                except ValueError:
                    pass
            j += 1

        # Parse properties
        props: Dict[str, str] = {}
        prop_start = prop_end = -1
        if j < len(lines) and lines[j].strip() == ":PROPERTIES:":
            prop_start = j
            j += 1
            while j < len(lines) and lines[j].strip() != ":END:":
                pm = re.match(r"^\s*:(\w+):\s*(.*)$", lines[j])
                if pm:
                    props[pm.group(1)] = pm.group(2).strip()
                j += 1
            if j < len(lines) and lines[j].strip() == ":END:":
                prop_end = j
                j += 1

        # Collect body
        body: List[str] = []
        while j < len(lines) and not re.match(r"^\*+\s", lines[j]):
            body.append(lines[j])
            j += 1

        # Determine if task qualifies for sweep
        ns_output = props.get("NIGHTSHIFT_OUTPUT", "").strip()
        if ns_output:
            i = j
            continue  # Has NIGHTSHIFT_OUTPUT — gh_reconcile handles these

        if _has_gh_ref(heading, props, body):
            i = j
            continue  # Has GitHub ref — gh_reconcile handles these

        if scheduled_date is None:
            i = j
            continue  # No scheduled date — can't assess staleness

        if scheduled_date >= cutoff:
            i = j
            continue  # Not old enough

        # Qualifies — queue for close
        reason = (
            f"one-time historical sweep: Review: task scheduled "
            f"{scheduled_date.isoformat()} ({(today - scheduled_date).days} days ago), "
            f"no artifact tracking ref; artifact consumed at time of nightshift execution."
        )
        to_close.append({
            "heading_line": i,
            "heading": heading,
            "prop_start": prop_start,
            "prop_end": prop_end,
            "reason": reason,
            "scheduled": scheduled_date.isoformat(),
            "days_past": (today - scheduled_date).days,
        })

        i = j

    if not to_close:
        return 0, []

    for t in to_close:
        closed.append({
            "org_file": str(org_file),
            "heading": t["heading"],
            "scheduled": t["scheduled"],
            "days_past": t["days_past"],
            "reason": t["reason"],
        })

    if dry_run:
        return len(to_close), closed

    # Apply bottom-up to preserve line indices
    ts = today_org_ts()
    for t in sorted(to_close, key=lambda x: x["heading_line"], reverse=True):
        hl = t["heading_line"]
        prop_end = t["prop_end"]

        # 1. Change state
        lines[hl] = RE_HEADING.sub(
            lambda m: m.group(1) + " DONE " + m.group(3)[0],
            lines[hl],
            count=1,
        )

        # 2. Inject CLOSED + RESULT + NIGHTSHIFT_RECONCILED
        reason_line = t["reason"][:160]
        new_props = [
            f"  :CLOSED: {ts}",
            f"  :RESULT: Auto-closed by review-sweep: {reason_line}",
            f"  :NIGHTSHIFT_RECONCILED: {ts}",
        ]

        if prop_end >= 0:
            for offset, pl in enumerate(new_props):
                lines.insert(prop_end + offset, pl)
        else:
            insert_at = hl + 1
            while insert_at < len(lines) and re.match(
                r"^\s*(SCHEDULED|DEADLINE|CLOSED):", lines[insert_at]
            ):
                insert_at += 1
            block = ["  :PROPERTIES:"] + new_props + ["  :END:"]
            for offset, bl in enumerate(block):
                lines.insert(insert_at + offset, bl)

    try:
        org_file.write_text("\n".join(lines), encoding="utf-8")
    except PermissionError:
        log.warning(f"Permission denied writing {org_file}")
        return 0, []

    return len(to_close), closed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=Path.home() / "Data")
    parser.add_argument("--days-stale", type=int, default=60,
                        help="Close Review: tasks scheduled more than N days ago (default: 60)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--output-json", type=Path, help="Write closed-task list to JSON")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    today = date(2026, 9, 8)  # canonical date from session
    data_dir = args.data_dir

    space_dirs = sorted(
        d for d in data_dir.iterdir()
        if d.is_dir()
        and re.match(r"^\d+-", d.name)
        and not d.name.endswith("-archive")
        and (d / "org").is_dir()
    )

    total_closed = 0
    all_closed = []

    for space_dir in space_dirs:
        org_file = space_dir / "org" / "next_actions.org"
        if not org_file.exists():
            continue

        n, closed = parse_and_sweep(org_file, args.days_stale, args.dry_run, today)
        if n:
            suffix = " (dry-run)" if args.dry_run else ""
            log.info(f"{space_dir.name}: {n} task(s) closed{suffix}")
            for c in closed:
                log.info(f"  CLOSE{suffix}: {c['heading'][:70]} (sched {c['scheduled']}, {c['days_past']}d ago)")
        total_closed += n
        all_closed.extend(closed)

    if args.output_json:
        args.output_json.write_text(json.dumps(all_closed, indent=2))
        log.info(f"Wrote {args.output_json}")

    suffix = " (dry-run)" if args.dry_run else ""
    print(f"\nreview-sweep complete: {total_closed} task(s) closed{suffix}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
