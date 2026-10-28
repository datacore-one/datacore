#!/usr/bin/env python3
"""Open continuation tasks, for /continue: the ledger first, then inbox files.

Tasks live in each space's ledger (DIP-0046). org/next_actions.org is only the
ledger's projection, and on a host whose phase-1 cycle has not run it does not
exist, so a search of the org files alone reported "no continuation tasks" on
dayshift while the ledgers held 192 (2026-10-23). The inbox files come second
because a task saved by /continue --save sits there until the next ingest.

    continuation_tasks.py [search term] [--json] [--root ~/Data]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ledger.fold import fold, org_task_state  # noqa: E402
from ledger.log import read_events  # noqa: E402

OPEN = {"TODO", "NEXT", "WAITING", "REVIEW"}
TAG = "continuation"


def from_ledger(space: Path) -> list[dict]:
    found = []
    for item in fold(read_events(space)).items.values():
        p = item.payload or {}
        state = org_task_state(item)
        if TAG not in (p.get("tags") or []) or state not in OPEN:
            continue
        props = (p.get("org") or {}).get("properties") or {}
        found.append({"source": "ledger", "space": space.name, "id": item.id, "state": state,
                      "title": item.title, "scheduled": p.get("scheduled") or "",
                      "bootstrap": props.get("BOOTSTRAP", "")})
    return sorted(found, key=lambda t: t["scheduled"])


def from_inbox(space: Path, known: set[str]) -> list[dict]:
    from org_workspace import OrgWorkspace, Query
    found = []
    for f in sorted((space / "org").glob("inbox.org")):
        ws = OrgWorkspace()
        ws.load(str(f))
        for n in Query(ws).by_tag(TAG):
            node_id = n.get_property("ID") or ""
            if n.todo not in OPEN or node_id in known:
                continue
            found.append({"source": "inbox", "space": space.name, "id": node_id, "state": n.todo,
                          "title": n.heading, "scheduled": str(n.scheduled or ""),
                          "bootstrap": n.get_property("BOOTSTRAP") or ""})
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("term", nargs="?", default="", help="match in title or BOOTSTRAP")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    spaces = sorted(p for p in Path(a.root).glob("[0-9]-*") if p.is_dir())
    tasks = []
    for space in spaces:
        if (space / ".datacore" / "events").is_dir():
            tasks += from_ledger(space)
    known = {t["id"] for t in tasks}
    try:
        for space in spaces:
            tasks += from_inbox(space, known)
    except ImportError as exc:
        print(f"inbox files NOT searched ({exc}); run with .datacore/venv/bin/python", file=sys.stderr)
    term = a.term.lower()
    tasks = [t for t in tasks if term in t["title"].lower() or term in t["bootstrap"].lower()]
    if a.json:
        print(json.dumps(tasks, ensure_ascii=False))
    else:
        for t in tasks:
            print(f'{t["source"]:6} {t["space"]:12} {t["state"]:7} {t["scheduled"][:16]:16} {t["title"]}  [{t["id"]}]')
    return 0


if __name__ == "__main__":
    sys.exit(main())
