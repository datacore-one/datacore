#!/usr/bin/env python3
"""One-time repair: un-delegate :AI: tasks that state no SURFACE/DONE_WHEN.

Applies the rule the delegation gate now enforces (chief-of-staff 3d89ce3) to
tasks tagged before it existed. On 2026-09-07 the gate tagged 27 follow-ups
with neither field; the org pre-commit hook then refused every commit in
0-personal, so the space stopped syncing and the morning briefing could not
reach the Mac.

The task is NOT deleted or deferred -- approval stands. It loses the :AI: tag
(nightshift cannot execute it) and gains :DELEGATION_BLOCKED: naming what is
missing, so it reads as the human task it actually is.

    undelegate_unexecutable.py <space> [--apply]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


from org_transaction import serialized

@serialized
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("space")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    root = Path.home() / "Data"
    sys.path.insert(0, str(root / ".datacore" / "lib"))
    from org_transaction import SafeOrgWorkspace as OrgWorkspace  # noqa: PLC0415
    # ONE definition of "executable", imported rather than restated. Writing
    # the rule again here first flagged 93 tasks against the gate's 27: it
    # missed that ACCEPTANCE_CRITERIA is DONE_WHEN's older spelling, and that
    # only TODO/NEXT are queued at all.
    from ai_task_gate import DONE_KEYS, QUEUED  # noqa: PLC0415

    space = Path(a.space) if Path(a.space).is_absolute() else root / a.space
    changed = []
    for name in ("inbox.org", "next_actions.org"):
        f = space / "org" / name
        if not f.exists():
            continue
        ws = OrgWorkspace()
        ws.load(str(f))
        for node in ws.all_nodes():
            tags = [t for t in (node.tags or [])]
            if "AI" not in [t.upper() for t in tags]:
                continue
            if (node.todo or "").upper() not in QUEUED:
                continue
            missing = []
            if not (node.get_property("SURFACE") or "").strip():
                missing.append("SURFACE")
            if not any((node.get_property(k) or "").strip() for k in DONE_KEYS):
                missing.append("DONE_WHEN")
            if not missing:
                continue
            changed.append((name, node.id(), node.heading[:58], ", ".join(missing)))
            if a.apply:
                ws.set_tags(node, [t for t in tags if t.upper() != "AI"])
                ws.set_property(node, "DELEGATION_BLOCKED", ", ".join(missing))
        if a.apply and changed:
            ws.save_all()
    for n, i, h, m in changed:
        print(f"  {n:<17} {h:<60} missing {m}")
    print(f"\n{len(changed)} task(s) {'un-delegated' if a.apply else 'would be un-delegated'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
