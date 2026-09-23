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

The fix that lasts is not a backfill, it is a gate, checked where the tag is
written:

    SURFACE                            which repo the work lands in   (LOCATE)
    DONE_WHEN or ACCEPTANCE_CRITERIA   how it knows it finished       (FINISH)
    ROADMAP                            which outcome it serves        (SELECT)
                                       — only in a space that HAS a roadmap

It is EQUAL to nightshift_parser._is_executable: both call
delegation_requirements.execution_gaps, ROADMAP clause included (owner decision
N8, 2026-09-23). A gate stricter than the executor rejects work the executor
would run happily, which teaches people to reach for --no-verify, after which
it guards nothing; a gate weaker than it admits work that is never run.

Normally nothing hits this gate, because sprint_sync.py writes the tag from
sprint.yaml and fills all three from fields the sprint already carries. It
fires on the hand-added exception — which is exactly the path that produced the
drift.

    python3 .datacore/lib/ai_task_gate.py <file.org> [...]      # exit 1 on fail
"""
from __future__ import annotations

import sys
from pathlib import Path

QUEUED = ("TODO", "NEXT")

# Equal to nightshift_parser._is_executable: one predicate, two callers. A gate
# STRICTER than the executor rejects work the executor would happily run, which
# teaches people to pass --no-verify and then guards nothing at all.
#
# ACCEPTANCE_CRITERIA is DONE_WHEN's older spelling and several well-specified
# tasks predate the rename; both are accepted.
DONE_KEYS = ("DONE_WHEN", "ACCEPTANCE_CRITERIA")

# ROADMAP is required only in a space that HAS a roadmap. 0-personal and
# 6-meridian have none, and demanding an epic link there is a complaint about a
# file that does not exist — the same scoping error agent_readiness made.
# The executor computes the same set, from the same function, for its data dir.
REPO = Path(__file__).resolve().parents[2]
from delegation_requirements import execution_gaps, roadmap_spaces, space_of  # noqa: E402

HAS_ROADMAP = roadmap_spaces(REPO)


def _space_of(path: Path) -> str:
    return space_of(path, REPO)


def _missing(props: dict, space: str, *, roadmap_spaces=None) -> list[str]:
    # Every clause comes from the executor's own predicate, not a copy of it.
    # The copy drifted: it compared "unassigned" case-sensitively where the
    # executor lower-cases, so SURFACE "Unassigned" passed this gate and was
    # then never run (DatacoreSpec/NightshiftGates.lean, AiGate). ROADMAP is in
    # the shared function too since decision N8.
    spaces = HAS_ROADMAP if roadmap_spaces is None else roadmap_spaces
    gaps = execution_gaps(props, roadmap_required=space in spaces)
    out = []
    if "SURFACE" in gaps:
        out.append("no SURFACE — the agent cannot tell which repo to work in")
    if "DONE_WHEN" in gaps:
        out.append("no DONE_WHEN — the agent cannot tell when it has finished")
    if "ROADMAP" in gaps:
        out.append("no ROADMAP — the agent cannot tell which outcome this serves")
    return out


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
        # Any tag STARTING with "AI", as find_ai_tasks selects -- not only the
        # exact tag "AI". A heading tagged :AIresearch: is queued by the
        # executor, so it must be gated here too.
        if node.todo not in QUEUED or not any(
                str(t).startswith("AI") for t in (node.shallow_tags or [])):
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
        missing = _missing(props, _space_of(Path(str(node.path))))
        if missing:
            bad.append((node.heading, missing, props.get("ID")))

    if not bad:
        return 0

    print(f"\n\033[1;31m{len(bad)} :AI: TASK(S) AN AGENT CANNOT EXECUTE\033[0m")
    print("The :AI: tag is nightshift's queue, not a classification.\n")
    for heading, missing, tid in bad[:15]:
        print(f"  {heading[:72]}")
        print(f"    {tid or '(no id)'}")
        for reason in missing:
            print(f"    - {reason}")
    if len(bad) > 15:
        print(f"  ...and {len(bad) - 15} more")
    print("\nEither give the task all three properties, or drop the :AI: tag —")
    print("the task stays in the pool either way, it just is not queued.")
    print("Sprint work should come from sprint.yaml:")
    print("  python3 .datacore/lib/sprint_sync.py --active --apply")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
