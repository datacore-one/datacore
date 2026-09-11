#!/usr/bin/env python3
"""Remove redundant open task copies, keeping the richest of each group.

Duplicates arise mechanically, not by hand: nightshift's review-queue re-emits
a `Review: ...` task every run until the underlying item is dealt with, so one
unhandled review became eight identical headings. Others are the same task
captured twice by different sessions. Either way the backlog overstates itself,
and a count that overstates is a count nobody trusts.

KEEP RULE, in order: most properties (the richest copy carries the CONTEXT and
ACCEPTANCE_CRITERIA that make a task actionable), then latest SCHEDULED (a
regenerated review carries the current date, its predecessors are stale), then
lowest id for determinism. Never merges bodies -- a merge that silently
combines two differently-worded tasks is worse than keeping one and losing the
other, because nobody can tell afterwards what was dropped.

Only open states are touched. DONE/archived history is left exactly as it is.

    dedup_tasks.py [--apply]      # default is a dry run
"""
from __future__ import annotations
import argparse, pathlib, re, sys
from collections import defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from org_transaction import SafeOrgWorkspace as OrgWorkspace
from spaces import discover_spaces  # noqa: E402

OPEN = {"TODO", "NEXT", "WAITING"}


def norm(h: str) -> str:
    return re.sub(r'^\[#[abc]\]\s*', '', re.sub(r'\s+', ' ', h).strip().lower())


def fingerprint(node):
    """Matching titles alone do not establish that task data is redundant."""
    return (
        node.heading, node.todo, node.priority, tuple(sorted(node.tags or [])),
        tuple(sorted((k, str(v)) for k, v in (node.properties or {}).items()
                     if k not in {"ID", "CREATED"})),
        str(node.scheduled) if node.scheduled else None,
        str(node.deadline) if node.deadline else None, node.body,
        tuple(fingerprint(child) for child in node.children),
    )


def retire_duplicate(ws, keep, duplicate):
    """Retain the original ID, body and descendants for references/recovery."""
    if fingerprint(keep) != fingerprint(duplicate):
        return False
    ws.transition(duplicate, "CANCELLED")
    ws.set_property(duplicate, "DEDUPE_OF", keep.id())
    ws.set_property(duplicate, "CLOSED_REASON", "identical active task retained under DEDUPE_OF")
    return True


from org_transaction import serialized

@serialized
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[2])
    args = ap.parse_args()

    removed = kept = 0
    for space in discover_spaces(args.root):
        f = space.path / "org" / "next_actions.org"
        if not f.exists():
            continue
        ws = OrgWorkspace(); ws.load(str(f))
        groups = defaultdict(list)
        for n in ws.all_nodes():
            if n.todo in OPEN and n.heading:
                groups[norm(n.heading)].append(n)
        dups = {k: v for k, v in groups.items() if len(v) > 1}
        if not dups:
            continue
        print(f"\n{f.parts[-3]}")
        for _, nodes in sorted(dups.items()):
            ranked = sorted(nodes, key=lambda n: (
                -len(n.properties or {}), str(n.scheduled or ""), str(n.id() or "")), reverse=False)
            # most properties first; among equals prefer the LATEST scheduled
            ranked = sorted(nodes, key=lambda n: (
                len(n.properties or {}), str(n.scheduled or "")), reverse=True)
            keep, drop = ranked[0], ranked[1:]
            kept += 1
            print(f"  KEEP  {keep.heading[:66]}  (props={len(keep.properties or {})}, sched={keep.scheduled or '-'})")
            for d in drop:
                if fingerprint(keep) != fingerprint(d):
                    print("  retain: matching title has different task data")
                    continue
                removed += 1
                print(f"  drop    id={d.id() or 'NO-ID'} props={len(d.properties or {})} sched={d.scheduled or '-'}")
                if args.apply:
                    retire_duplicate(ws, keep, d)
        if args.apply:
            ws.save(str(f))

    verb = "retired (data preserved)" if args.apply else "would retire (data preserved)"
    print(f"\n{verb} {removed} redundant copies across {kept} duplicated headings")
    if not args.apply:
        print("dry run — re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
