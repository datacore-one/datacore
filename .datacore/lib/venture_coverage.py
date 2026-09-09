#!/usr/bin/env python3
"""Where is work missing? Answered top-down, per venture, without demanding docs.

WHY THIS EXISTS. Weekly planning read `next_actions.org` and nothing else. A task
list records where attention already went, so planning from it reproduces last
week's allocation and cannot show what was neglected — neglect is an absence, and
absences are invisible in a list of what exists.

Measured 2026-09-09: Datacore infra held 155 open tasks (the thing being worked
on) while the raise held 25 — but 9 of its tasks were priority A, the densest
concentration in the file. Investor meetings taken, across nineteen deck
versions: zero. No amount of task querying surfaces that, because zero meetings
generate zero tasks.

The pieces to answer it properly already existed and were never joined:
  roadmap_validate.py --coverage   intents that no roadmap item serves
  intent_tasks.py                  open tasks placed on the intent graph
  <space>/org/intents.org          the stated why, where it has been written
  .datacore/cos/priorities.yaml    the owner's ranking, with a 30-day freshness
                                   rule its own header states and nothing enforced

DORMANT VENTURES ARE NOT FAILURES. A space without a roadmap or an intent graph
is usually parked for bandwidth, not broken. Demanding the documents back is how
a report becomes noise nobody reads. Those spaces get the basic reading — open
count, last closure, oldest overdue — and no complaint.

    venture_coverage.py                 # all spaces
    venture_coverage.py --space 5-plur
    venture_coverage.py --json
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRIORITIES = Path.home() / "Data" / ".datacore" / "cos" / "priorities.yaml"
VENTURES = REPO / ".datacore" / "registry" / "ventures.yaml"
STALE_DAYS = 30


def _spaces() -> list[Path]:
    return sorted(p for p in REPO.glob("[0-9]-*") if p.is_dir())


def _mode(space: Path) -> str:
    """planned = intents + roadmap · partial = intents only · basic = neither."""
    intents = (space / "org" / "intents.org").exists()
    roadmap = (space / "roadmap.yaml").exists()
    if intents and roadmap:
        return "planned"
    if intents:
        return "partial"
    return "basic"


def _tasks(space: Path) -> dict:
    """Open / overdue / last-closure for a space. Deduplicated by heading, because
    next_actions.org carries repeated copies of some tasks and every count over it
    is otherwise inflated."""
    sys.path.insert(0, str(REPO / ".datacore" / "lib"))
    try:
        from org_workspace import OrgWorkspace
    except Exception:
        return {}
    files = [f for f in (space / "org").glob("*.org")
             if f.name not in {"nightshift.org"} and "archive" not in f.name]
    if not files:
        return {}
    ws = OrgWorkspace()
    for f in files:
        try:
            ws.load(f)
        except Exception:
            continue
    today = date.today().isoformat()
    seen, closed = {}, []
    for n in ws.all_nodes():
        st = getattr(n, "todo", None)
        if st in ("TODO", "NEXT", "WAITING", "REVIEW"):
            seen.setdefault((n.heading or "").strip(), n)
        c = str(getattr(n, "closed", "") or "")
        if c:
            closed.append(c[1:11])
    uniq = list(seen.values())
    overdue = [n for n in uniq
               if str(getattr(n, "scheduled", "") or "")[1:11]
               and str(n.scheduled)[1:11] < today]
    return {"open": len(uniq),
            "priority_a": sum(1 for n in uniq if getattr(n, "priority", None) == "A"),
            "overdue": len(overdue),
            "last_closed": max(closed) if closed else None}


def _coverage(space: Path) -> dict:
    """Intents that no roadmap item serves. Reuses roadmap_validate rather than
    reimplementing the traversal."""
    r = subprocess.run(
        [sys.executable, str(REPO / ".datacore" / "lib" / "roadmap_validate.py"),
         "--space", space.name, "--coverage"],
        capture_output=True, text=True, timeout=300)
    out = r.stdout
    m = re.search(r"item-bearing intents:\s*(\d+)\s+covered:\s*(\d+)", out)
    gaps = re.findall(r"^\s+-\s+(\S+)\s+\((\w+)\)", out, re.M)
    return {"intents": int(m.group(1)) if m else None,
            "covered": int(m.group(2)) if m else None,
            "gaps": [{"id": a, "kind": b} for a, b in gaps]}


def _priorities_age() -> tuple[str | None, int | None]:
    if not PRIORITIES.exists():
        return None, None
    m = re.search(r"^updated:\s*([0-9-]+)", PRIORITIES.read_text(), re.M)
    if not m:
        return None, None
    u = m.group(1)
    y, mo, d = (int(x) for x in u.split("-"))
    return u, (date.today() - date(y, mo, d)).days


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    spaces = [REPO / args.space] if args.space else _spaces()
    report = {"date": date.today().isoformat(), "spaces": {}}

    upd, age = _priorities_age()
    report["priorities"] = {"updated": upd, "age_days": age,
                            "stale": bool(age and age > STALE_DAYS)}

    for sp in spaces:
        if not sp.is_dir():
            continue
        mode = _mode(sp)
        row = {"mode": mode, "tasks": _tasks(sp)}
        if mode == "planned":
            try:
                row["coverage"] = _coverage(sp)
            except Exception as e:
                row["coverage"] = {"error": str(e)[:120]}
        report["spaces"][sp.name] = row

    if args.json:
        print(json.dumps(report, indent=1))
        return 0

    if report["priorities"]["stale"]:
        print(f"!! priorities.yaml is {age} days old (updated {upd}). Its own header "
              f"says >{STALE_DAYS}d must be reconfirmed, not assumed.\n")

    print(f"{'space':14} {'mode':8} {'open':>5} {'A':>4} {'overdue':>8} "
          f"{'last close':>11}  coverage")
    for name, row in report["spaces"].items():
        t = row.get("tasks") or {}
        cov = row.get("coverage") or {}
        if cov.get("intents"):
            c = f"{cov['covered']}/{cov['intents']} intents"
            if cov.get("gaps"):
                c += f" — {len(cov['gaps'])} uncovered"
        elif row["mode"] == "partial":
            c = "intents, no roadmap"
        else:
            c = "basic mode (dormant)"
        print(f"{name:14} {row['mode']:8} {t.get('open',0):5} {t.get('priority_a',0):4} "
              f"{t.get('overdue',0):8} {str(t.get('last_closed') or '-'):>11}  {c}")

    print("\nWORK WITH NO ROADMAP ITEM BEHIND IT — gaps, or deletion candidates:")
    any_gap = False
    for name, row in report["spaces"].items():
        for g in (row.get("coverage") or {}).get("gaps", []):
            print(f"  {name:14} {g['id']}  ({g['kind']})")
            any_gap = True
    if not any_gap:
        print("  none in the spaces that carry a roadmap")

    dormant = [n for n, r in report["spaces"].items() if r["mode"] != "planned"]
    if dormant:
        print(f"\nNo roadmap, so no gap analysis possible: {', '.join(dormant)}")
        print("  Dormant is a bandwidth decision, not a defect. These get the basic")
        print("  reading above and no demand for documents.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
