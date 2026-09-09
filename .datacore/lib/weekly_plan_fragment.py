#!/usr/bin/env python3
"""The weekly plan, in a form something other than a human can read.

WHY THIS EXISTS. On 2026-09-09 a week was planned in detail — priorities,
sequencing thesis, metrics to move, falsifiable predictions — and written to
`0-personal/notes/pages/weekly-plan-<week>.md`. Nothing read it. Verified three
ways: no code references the path, `/today` contains zero mentions of "weekly",
and Winston's fragment directory held exactly one file, `health.json`.

So the plan guided nobody. Every morning started from tasks and calendar as if no
week had been planned, and Friday's review had to re-read prose to ask whether
the week held.

Same defect class as the eight briefing sections lost when `/today` was disabled:
a producer writes, no consumer reads.

This puts the plan on the contract The Practice already uses for health —
`~/.datacore/cos/fragments/<date>/<name>.json` with schema_version, composed_at,
composed_by and date — so the briefing can consume it like any other fragment.

WRITTEN ONCE A WEEK, READ EVERY DAY. The fragment lands in the folder for the day
it was written; `read` looks back up to seven days for the most recent one. Its
age is returned with it, because a plan written Monday and consulted Friday is
still the plan but is no longer news, and a briefing that forgets the difference
will guide Wednesday with Monday's assumptions.

    weekly_plan_fragment.py write --file plan.json
    weekly_plan_fragment.py read [--date YYYY-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

FRAGMENTS = Path.home() / ".datacore" / "cos" / "fragments"
NAME = "weekly-plan.json"
LOOKBACK_DAYS = 7
SCHEMA = "1"


def write(payload: dict, when: date | None = None) -> Path:
    when = when or date.today()
    d = FRAGMENTS / when.isoformat()
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "schema_version": SCHEMA,
        "composed_at": datetime.now(timezone.utc).isoformat(),
        "composed_by": "weekly-plan",
        "date": when.isoformat(),
    }
    doc.update(payload)
    out = d / NAME
    out.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return out


def read(on: date | None = None) -> dict | None:
    """The most recent weekly plan within the lookback, with its age attached."""
    on = on or date.today()
    for back in range(LOOKBACK_DAYS + 1):
        day = on - timedelta(days=back)
        f = FRAGMENTS / day.isoformat() / NAME
        if not f.exists():
            continue
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        doc["_age_days"] = back
        doc["_path"] = str(f)
        # A plan is not stale at four days old — it is a weekly plan. It is stale
        # when it has outlived the week it planned.
        doc["_expired"] = back > LOOKBACK_DAYS - 1
        return doc
    return None


def _render(doc: dict) -> str:
    age = doc.get("_age_days", 0)
    when = {0: "written today", 1: "written yesterday"}.get(age, f"day {age + 1} of the week it planned")
    out = [f"## The week this day belongs to", "",
           f"**{doc.get('week', 'the current week')}** — {when}."]
    if doc.get("thesis"):
        out += ["", f"*Sequencing thesis:* {doc['thesis']}"]
    if doc.get("priorities"):
        out += ["", "**Priorities, in the order that decides what stops without them:**"]
        for i, p in enumerate(doc["priorities"], 1):
            if isinstance(p, dict):
                out.append(f"{i}. **{p.get('what','')}** — {p.get('why','')}")
            else:
                out.append(f"{i}. {p}")
    if doc.get("metrics_targeted"):
        out += ["", "**Numbers this week means to move:**"]
        for m in doc["metrics_targeted"]:
            out.append(f"- {m.get('id')}: now {m.get('now')}, target {m.get('target')}")
    if doc.get("predictions"):
        out += ["", "**Predictions frozen for the review** — check them, do not restate them:"]
        for p in doc["predictions"]:
            out.append(f"- {p}")
    if doc.get("yields"):
        out += ["", f"**Named to yield if the week is full:** {', '.join(doc['yields'])}"]
    if doc.get("days"):
        today = date.today().isoformat()
        what = doc["days"].get(today)
        if what:
            out += ["", f"**Today was planned as:** {what}"]
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write")
    w.add_argument("--file", required=True, help="JSON payload")
    w.add_argument("--date")
    r = sub.add_parser("read")
    r.add_argument("--date")
    r.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.cmd == "write":
        payload = json.loads(Path(a.file).read_text(encoding="utf-8"))
        when = date.fromisoformat(a.date) if a.date else None
        print(f"wrote {write(payload, when)}")
        return 0

    on = date.fromisoformat(a.date) if a.date else None
    doc = read(on)
    if doc is None:
        print("No weekly plan fragment in the last "
              f"{LOOKBACK_DAYS} days. The week was not planned, or /weekly-plan "
              "did not write one — say so in the briefing rather than filling "
              "the gap from the task list.")
        return 1
    print(json.dumps(doc, indent=1) if a.json else _render(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
