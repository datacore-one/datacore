#!/usr/bin/env python3
"""Build standup-block inputs for the journal-entry-writer agent.

Produces three deterministic fields the agent feeds into the ``## Standup``
block of a team journal:

- ``accomplishment_task_id_map``: each accomplishment line matched (by
  heading substring) against ``{space}/org/next_actions.org``. The match
  prefers DONE-state tasks closed within the last 3 days (assumes the
  accomplishment IS that closure), falling back to any state.
- ``planned_today``: items the contributor commits to next. Sources, in
  order: explicit ``--continuation`` arg, then NEXT-state tasks tagged
  ``:standup:`` for the contributor, else empty.
- ``blockers``: WAITING tasks tagged ``:standup:`` for the contributor that
  are at least ``--blocker-threshold`` days old (default 3).

Output is JSON to stdout. The journal-coordinator captures this and threads
it into the journal-entry-writer prompt.

Usage:
    python3 .datacore/lib/standup_inputs.py \\
        --space 1-datafund \\
        --contributor plur9 \\
        --accomplishments-file /tmp/accomplishments.txt \\
        [--continuation "Polish the spec"] \\
        [--blocker-threshold 3]

Or pass accomplishments as a JSON-array via ``--accomplishments-json``.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).parent))

from org_workspace import OrgWorkspace, Query  # noqa: E402


_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "to", "for", "in", "on", "at",
    "by", "with", "from", "as", "is", "was", "be", "been", "being",
    "this", "that", "these", "those", "it", "its", "into", "via",
}


def _tokens(text: str) -> set[str]:
    """Lowercase content tokens, stop-words removed."""
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if len(t) >= 3 and t.lower() not in _STOPWORDS
    }


def _score(accomplishment: str, heading: str) -> float:
    """Token-overlap Jaccard score between accomplishment and task heading."""
    a = _tokens(accomplishment)
    b = _tokens(heading)
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _parse_org_date(value: str | None) -> date | None:
    """Extract a YYYY-MM-DD date from common org timestamp shapes."""
    if not value:
        return None
    match = _DATE_RE.search(value)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _load_tasks(org_file: Path) -> list[dict]:
    """Load all tasks from an org file as plain dicts (decoupled from NodeView)."""
    if not org_file.exists():
        return []
    ws = OrgWorkspace()
    ws.load(str(org_file))
    q = Query(ws)
    out: list[dict] = []
    for state in ("TODO", "NEXT", "WAITING", "REVIEW", "DONE"):
        for node in q.by_state(state):
            tags = list(getattr(node, "tags", []) or [])
            assignee = (node.get_property("ASSIGNEE") or "").lower()
            created = _parse_org_date(node.get_property("CREATED"))
            closed = _parse_org_date(node.get_property("CLOSED"))
            # Body sometimes carries CLOSED inline (DONE [#A] ... CLOSED: [...])
            if not closed:
                body_blob = (node.heading or "") + " " + (node.body or "")
                closed = _parse_org_date(body_blob)
            out.append({
                "id": node.id() if hasattr(node, "id") else node.get_property("ID"),
                "heading": node.heading or "",
                "state": state,
                "tags": tags,
                "assignee": assignee,
                "created": created.isoformat() if created else None,
                "closed": closed.isoformat() if closed else None,
            })
    return out


def map_accomplishments_to_ids(
    accomplishments: list[str],
    tasks: list[dict],
    *,
    min_score: float = 0.25,
    recent_close_days: int = 3,
) -> list[dict]:
    """For each accomplishment, find the best-matching org task (or None).

    Prefers tasks closed in the last ``recent_close_days`` days when scores
    tie or are within 0.05 of each other — the assumption is past-tense
    accomplishments correspond to recently-closed tasks.
    """
    today = date.today()
    cutoff = today - timedelta(days=recent_close_days)
    out: list[dict] = []
    for acc in accomplishments:
        best: dict | None = None
        best_score = 0.0
        best_recent = False
        for t in tasks:
            score = _score(acc, t["heading"])
            if score < min_score:
                continue
            closed_dt = _parse_org_date(t.get("closed"))
            recent = bool(closed_dt and closed_dt >= cutoff and t["state"] == "DONE")
            # Prefer recently-closed when within 0.05 of best
            better = (
                score > best_score + 0.05
                or (abs(score - best_score) <= 0.05 and recent and not best_recent)
            )
            if best is None or better:
                best = t
                best_score = score
                best_recent = recent
        out.append({
            "text": acc,
            "id": best["id"] if best else None,
            "match_score": round(best_score, 3) if best else 0.0,
            "match_state": best["state"] if best else None,
        })
    return out


def planned_today(
    tasks: list[dict],
    *,
    contributor: str,
    continuation: str | None,
) -> list[dict]:
    """Items for the Standup #### Today block.

    Priority:
    1. ``--continuation`` text (single item without org-id)
    2. NEXT-state tasks tagged ``standup`` assigned to ``contributor``
    """
    contributor_lc = contributor.lower()
    items: list[dict] = []
    if continuation:
        items.append({"heading": continuation.strip(), "id": None, "source": "continuation"})

    for t in tasks:
        if t["state"] != "NEXT":
            continue
        if "standup" not in t["tags"]:
            continue
        if t["assignee"] and t["assignee"] != contributor_lc:
            continue
        items.append({"heading": t["heading"], "id": t["id"], "source": "next_actions"})
    return items


def blockers(
    tasks: list[dict],
    *,
    contributor: str,
    threshold_days: int,
) -> list[dict]:
    """WAITING tasks ``:standup:`` for ``contributor`` older than threshold."""
    contributor_lc = contributor.lower()
    today = date.today()
    cutoff = today - timedelta(days=threshold_days)
    out: list[dict] = []
    for t in tasks:
        if t["state"] != "WAITING":
            continue
        if "standup" not in t["tags"]:
            continue
        if t["assignee"] and t["assignee"] != contributor_lc:
            continue
        created = _parse_org_date(t.get("created"))
        if not created or created > cutoff:
            continue
        out.append({"heading": t["heading"], "since": created.isoformat(), "id": t["id"]})
    return out


def build(
    *,
    space: str,
    contributor: str,
    accomplishments: list[str],
    continuation: str | None = None,
    blocker_threshold: int = 3,
) -> dict:
    org_file = Path(space) / "org" / "next_actions.org"
    tasks = _load_tasks(org_file)
    return {
        "space": space,
        "contributor": contributor,
        "accomplishment_task_id_map": map_accomplishments_to_ids(accomplishments, tasks),
        "planned_today": planned_today(tasks, contributor=contributor, continuation=continuation),
        "blockers": blockers(tasks, contributor=contributor, threshold_days=blocker_threshold),
        "stats": {
            "total_tasks": len(tasks),
            "accomplishments": len(accomplishments),
        },
    }


def _read_accomplishments(args: argparse.Namespace) -> list[str]:
    if args.accomplishments_json:
        data = json.loads(args.accomplishments_json)
        if not isinstance(data, list):
            raise SystemExit("--accomplishments-json must be a JSON array of strings")
        return [str(x).strip() for x in data if str(x).strip()]
    if args.accomplishments_file:
        path = Path(args.accomplishments_file)
        if not path.exists():
            raise SystemExit(f"file not found: {path}")
        return [
            line.strip().lstrip("- ").strip()
            for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    if not sys.stdin.isatty():
        return [
            line.strip().lstrip("- ").strip()
            for line in sys.stdin.read().splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
    return []


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build standup-block inputs for journal-entry-writer.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--space", required=True, help="Space directory (e.g. 1-datafund)")
    parser.add_argument("--contributor", required=True, help="Contributor name (lowercase, no @)")
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--accomplishments-file", help="Path to file (one accomplishment per line)")
    g.add_argument("--accomplishments-json", help="JSON-encoded list of accomplishment strings")
    parser.add_argument("--continuation", help="Explicit continuation text for #### Today")
    parser.add_argument("--blocker-threshold", type=int, default=3, help="Days before WAITING is a blocker")
    args = parser.parse_args(list(argv) if argv is not None else None)

    accs = _read_accomplishments(args)
    result = build(
        space=args.space,
        contributor=args.contributor,
        accomplishments=accs,
        continuation=args.continuation,
        blocker_threshold=args.blocker_threshold,
    )
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
