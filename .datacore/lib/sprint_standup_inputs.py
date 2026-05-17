#!/usr/bin/env python3
"""Sprint context for the async standup drafter.

Reads the active sprint.yaml and outputs a standup-ready JSON block:
  - sprint metadata (id, day_of_sprint, sprint_length, status)
  - shipped: items with state=done (all, not just overnight — caller can filter)
  - in_flight: items with state in {claimed, in-progress, review}
  - blocked: items with state=blocked
  - hitl_pending: hitl_log entries with classification != "decided"
  - progress: counts by state

Usage:
    python3 .datacore/lib/sprint_standup_inputs.py \\
        --sprint ~/Data/5-plur/2-projects/enterprise/sprints/2026-W20-sprint1.yaml

    python3 .datacore/lib/sprint_standup_inputs.py \\
        --sprint-dir ~/Data/5-plur/2-projects/enterprise/sprints  # picks latest

Output is JSON to stdout. Integrate into the standup-generator agent's
Phase 0 input collection step.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
        try:
            dt = datetime.strptime(str(value)[:len(fmt) + 5], fmt)
            return dt.date()
        except ValueError:
            continue
    # Last try: just grab first 10 chars
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ="safe")
        with path.open() as f:
            return yaml.load(f) or {}
    except ImportError:
        import yaml as pyyaml  # type: ignore[import]
        with path.open() as f:
            return pyyaml.safe_load(f) or {}


def _latest_sprint(sprint_dir: Path) -> Path | None:
    yamls = sorted(sprint_dir.glob("2026-W*-sprint*.yaml"), reverse=True)
    return yamls[0] if yamls else None


def _day_of_sprint(start: str | None, end: str | None, today: date) -> tuple[int, int]:
    """Return (day_number, total_days) — 1-indexed, clamped."""
    s = _parse_date(start)
    e = _parse_date(end)
    if not s or not e:
        return (0, 0)
    total = (e - s).days + 1
    day = min(max((today - s).days + 1, 1), total)
    return (day, total)


def extract(sprint: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    sprint_id = sprint.get("sprint_id", "unknown")
    status = sprint.get("status", "unknown")
    dates = sprint.get("dates", {})
    day, total = _day_of_sprint(dates.get("start"), dates.get("end"), today)

    # Build claims map: item_id → {actor, started, branch, pr}
    claims_by_item: dict[str, dict] = {}
    for c in sprint.get("claims", []):
        item_id = c.get("item")
        if item_id:
            # Keep the latest claim per item
            existing = claims_by_item.get(item_id)
            if not existing or str(c.get("started", "")) > str(existing.get("started", "")):
                claims_by_item[item_id] = c

    shipped: list[dict] = []
    in_flight: list[dict] = []
    blocked: list[dict] = []
    ready_count = 0
    counts: dict[str, int] = {}

    for item in sprint.get("backlog", []):
        state = item.get("state", "ready")
        counts[state] = counts.get(state, 0) + 1
        item_id = item.get("id", "")
        claim = claims_by_item.get(item_id, {})

        entry = {
            "id": item_id,
            "title": item.get("title", ""),
            "actor": claim.get("actor") or item.get("claimed_by"),
            "priority": item.get("priority", ""),
            "ref": item.get("ref", ""),
            "branch": claim.get("branch"),
            "pr": claim.get("pr"),
            "started": claim.get("started"),
        }

        if state == "done":
            shipped.append(entry)
        elif state in ("claimed", "in-progress", "review"):
            entry["state"] = state
            in_flight.append(entry)
        elif state == "blocked":
            blocked.append(entry)
        elif state == "ready":
            ready_count += 1

    # HITL pending
    hitl_pending = [
        h for h in sprint.get("hitl_log", [])
        if h.get("classification", "undecided") not in ("decided", "systematic", "avoidable", "defer")
    ]

    return {
        "sprint_id": sprint_id,
        "status": status,
        "day_of_sprint": day,
        "sprint_length": total,
        "dates": {
            "start": dates.get("start"),
            "end": dates.get("end"),
        },
        "goal": (sprint.get("goal") or "").strip(),
        "shipped": shipped,
        "in_flight": in_flight,
        "blocked": blocked,
        "hitl_pending": hitl_pending,
        "ready_remaining": ready_count,
        "progress": {
            "done": counts.get("done", 0),
            "review": counts.get("review", 0),
            "in_progress": counts.get("in-progress", 0),
            "claimed": counts.get("claimed", 0),
            "blocked": counts.get("blocked", 0),
            "ready": counts.get("ready", 0),
            "total": sum(counts.values()),
        },
    }


def _resolve_sprint_path(args: argparse.Namespace) -> Path | None:
    if args.sprint:
        p = Path(args.sprint).expanduser()
        if not p.exists():
            print(f"sprint file not found: {p}", file=sys.stderr)
            return None
        return p
    if args.sprint_dir:
        d = Path(args.sprint_dir).expanduser()
        p = _latest_sprint(d)
        if not p:
            print(f"no sprint files found in {d}", file=sys.stderr)
            return None
        return p
    # Default: look in the enterprise sprints dir relative to this file
    default_dir = Path(__file__).resolve().parents[3] / "5-plur" / "2-projects" / "enterprise" / "sprints"
    p = _latest_sprint(default_dir)
    if not p:
        print(f"no sprint files found in {default_dir}", file=sys.stderr)
        return None
    return p


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sprint context JSON for standup drafter",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--sprint", help="Path to sprint YAML file")
    g.add_argument("--sprint-dir", help="Directory containing sprint files (picks latest)")
    parser.add_argument("--date", help="Override today's date (YYYY-MM-DD)")
    args = parser.parse_args(argv)

    sprint_path = _resolve_sprint_path(args)
    if not sprint_path:
        return 1

    today = date.fromisoformat(args.date) if args.date else date.today()
    sprint = _load_yaml(sprint_path)
    result = extract(sprint, today)
    result["_source"] = str(sprint_path)

    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
