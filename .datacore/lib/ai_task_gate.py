#!/usr/bin/env python3
"""Refuse an :AI: task an agent could not actually execute.

`:AI:` is not a label meaning "an agent could do this one day". It is a QUEUE:
nightshift_parser.find_ai_tasks selects exactly the org headings tagged :AI: in
state TODO or NEXT, and hands them to an agent overnight. So the tag is a
promise that the task is executable as written.

On 2026-09-04 that promise was false for 88 of the 92 tasks holding the tag —
they named no surface, so the agent could not tell which repo, and no
definition of done, so it could not tell whether it had succeeded. The pool had
drifted there gradually: every hand-added `:AI:`, every agent filing follow-up
work, every cadence, each individually reasonable.

The fix that lasts is not a backfill, it is a gate. Three properties, checked
where the tag is written:

    ROADMAP    which outcome this serves        (SELECT)
    SURFACE    which repo the work lands in     (LOCATE)
    DONE_WHEN  how the agent knows it finished  (FINISH)

Normally nothing hits this gate, because sprint_sync.py writes the tag from
sprint.yaml and fills all three from fields the sprint already carries. It
fires on the hand-added exception — which is exactly the path that produced the
drift.

    python3 .datacore/lib/ai_task_gate.py <file.org> [...]      # exit 1 on fail
"""
from __future__ import annotations

import sys
from pathlib import Path

REQUIRED = {
    "ROADMAP": "no ROADMAP — the agent cannot tell which outcome this serves",
    "SURFACE": "no SURFACE — the agent cannot tell which repo to work in",
    "DONE_WHEN": "no DONE_WHEN — the agent cannot tell when it has finished",
}
QUEUED = ("TODO", "NEXT")


def main(argv: list[str]) -> int:
    files = [Path(a) for a in argv if a.endswith(".org")]
    if not files:
        return 0

    from org_workspace import OrgWorkspace

    ws = OrgWorkspace()
    for f in files:
        if f.exists():
            ws.load(f)

    bad = []
    for node in ws.all_nodes():
        # shallow_tags, not tags: nightshift reads the heading line and does
        # not inherit tags from ancestors, so an inherited :AI: is not queued
        # and must not be gated as though it were. See agent_readiness.tasks().
        if node.todo not in QUEUED or "AI" not in (node.shallow_tags or []):
            continue
        props = node.properties or {}
        # A queue REFERENCE deliberately carries no spec — SOURCE_ID points at
        # the task that holds it, so that one record cannot drift from the copy
        # that runs (task_queue.resolve_queued_task merges them at selection
        # time, and REFUSES when the pointer dangles). Demanding the spec on
        # both ends would force the duplication the reference design exists to
        # prevent. The source task is gated on its own terms.
        if str(props.get("SOURCE_ID") or "").strip():
            continue
        missing = [k for k in REQUIRED if not str(props.get(k) or "").strip()
                   or str(props.get(k)).strip() == "unassigned"]
        if missing:
            bad.append((node.heading, missing, props.get("ID")))

    if not bad:
        return 0

    print(f"\n\033[1;31m{len(bad)} :AI: TASK(S) AN AGENT CANNOT EXECUTE\033[0m")
    print("The :AI: tag is nightshift's queue, not a classification.\n")
    for heading, missing, tid in bad[:15]:
        print(f"  {heading[:72]}")
        print(f"    {tid or '(no id)'}")
        for k in missing:
            print(f"    - {REQUIRED[k]}")
    if len(bad) > 15:
        print(f"  ...and {len(bad) - 15} more")
    print("\nEither give the task all three properties, or drop the :AI: tag —")
    print("the task stays in the pool either way, it just is not queued.")
    print("Sprint work should come from sprint.yaml:")
    print("  python3 .datacore/lib/sprint_sync.py --active --apply")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
