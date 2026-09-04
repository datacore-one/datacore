#!/usr/bin/env python3
"""Project a sprint into the agent queue, and keep the queue equal to the sprint.

THE PROBLEM THIS SOLVES. Two things both claimed to say what agents work on and
neither read the other. `sprint.yaml` was written, reviewed and never consumed —
nothing in the tree read it, and the `miles_routing.sprint_tag_filter` field had
zero readers. Meanwhile nightshift selects work by one rule: an org heading
tagged `:AI:` in state TODO or NEXT (see nightshift_parser.find_ai_tasks). So the
sprint was a document and the queue was a tag, and on 2026-09-04 the tag held 92
tasks of which 4 named a surface and a definition of done. The other 88 would
have been picked up by an agent that could not tell which repo they belonged to
or how it would know it had finished.

THE FIX IS A DIRECTION, NOT A SYNC. `sprint.yaml` is the source; the `:AI:` tag
is a projection of it. This script is the ONLY writer of that tag:

    in sprint + claimed by an agent   ->  org task exists, tagged :AI:
    not in sprint                     ->  :AI: removed (the task itself stays)

Removing the tag does not delete, defer or deprioritise anything. It says "not
this sprint", which is what a sprint means. The task keeps its ROADMAP link and
waits in the pool for a sprint to pull it.

WHY IT REFUSES. A sprint item claimed by an agent must carry roadmap, milestone,
surface and acceptance. Those are exactly the four things agent_readiness.py
checks, and sprint.yaml already had fields for all of them. Refusing here is the
point: an underspecified item is caught while a human is still looking at the
sprint, instead of at 02:00 by an agent that cannot act on it.

    python3 .datacore/lib/sprint_sync.py --active            # dry run
    python3 .datacore/lib/sprint_sync.py --sprint 2026-W37-core-sprint1 --apply
    python3 .datacore/lib/sprint_sync.py --active --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]

# A claimer that is an agent. Everything else is a person, and a person's item
# is NOT projected into the agent queue however well specified it is.
AGENT_CLAIMER = re.compile(r"nightshift|miles|tris|agent|winston", re.I)

PRIORITY = {"must": "A", "should": "B", "could": "C"}

# Fields an agent-claimed item must carry. These mirror agent_readiness.py's
# LOCATE / SELECT / FINISH gates one-for-one; keep them in step.
REQUIRED = ("title", "roadmap", "milestone", "surface", "acceptance")

# In-flight work is never un-queued mid-run: pulling the tag out from under a
# running agent orphans the task and the agent's own completion write lands on
# a heading that no longer claims to be its work.
IN_FLIGHT = {"executing", "requeued", "claimed"}

# ...but the hold has to expire, or it protects a corpse. The enterprise issue
# sweep sat at NIGHTSHIFT_STATUS: executing for SEVEN DAYS with two requeues
# and score 0.0, its COMPLETED stamp predating its STARTED stamp. Nothing was
# running; the status was simply never cleared, and an unexpiring exemption
# meant every sync politely stepped around it. A run that has not moved in this
# many days is stuck, and stuck work is un-queued and named, not sheltered.
STALE_AFTER_DAYS = 2
STAMPS = ("NIGHTSHIFT_STARTED", "NIGHTSHIFT_ROUTED", "NIGHTSHIFT_COMPLETED")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def one_line(s: str) -> str:
    """Acceptance prose -> a single-line DONE_WHEN an org property can hold.

    Rejoin words the sprint's own line-wrapping split across a hyphen first:
    YAML folds "thirty-\\neight" and a naive whitespace collapse turns that
    into "thirty- eight". Only rejoin when the next character is lowercase, so
    genuine compounds ("self-hosted", "no-benchmark-war") keep their hyphen.
    """
    s = re.sub(r"-\n(?=[a-z])", "", (s or ""))
    return re.sub(r"\s+", " ", s.strip())


def _age_days(props: dict) -> float | None:
    """Days since this task last showed a sign of life. None if it never has."""
    from datetime import datetime, timezone

    newest = None
    for k in STAMPS:
        raw = str(props.get(k) or "").strip()
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if newest is None or dt > newest:
            newest = dt
    if newest is None:
        return None
    return (datetime.now(timezone.utc) - newest).total_seconds() / 86400


def discover(space: str) -> list[Path]:
    return sorted((REPO / space).glob("2-projects/*/sprints/*/sprint.yaml"))


def pick(space: str, want: str | None, active: bool) -> list[Path]:
    found = discover(space)
    if want:
        hit = [p for p in found if want in str(p)]
        if not hit:
            sys.exit(f"no sprint matching {want!r} under {space}/2-projects/*/sprints/")
        return hit
    if active:
        out = []
        for p in found:
            d = yaml.safe_load(p.read_text()) or {}
            if d.get("status") == "active":
                out.append(p)
        return out
    return found


def items_of(sprint: dict, include_stretch: bool) -> list[dict]:
    rows = list(sprint.get("backlog") or [])
    if include_stretch:
        rows += list(sprint.get("stretch") or [])
    return rows


def agent_items(sprint: dict, include_stretch: bool) -> tuple[list[dict], list[str]]:
    """Split the sprint's items into agent-claimed and everything else."""
    mine, problems = [], []
    for it in items_of(sprint, include_stretch):
        claimer = str(it.get("default_claimer") or it.get("owner") or "")
        if not AGENT_CLAIMER.search(claimer):
            continue
        missing = [f for f in REQUIRED if not it.get(f)]
        if missing:
            problems.append(
                f"{it.get('id', '?')} ({claimer}) is claimed by an agent but has no "
                f"{', '.join(missing)} — an agent cannot "
                + ("find the repo" if "surface" in missing else "know it finished")
            )
            continue
        mine.append(it)
    return mine, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default="5-plur")
    ap.add_argument("--sprint", help="sprint id or path fragment")
    ap.add_argument("--active", action="store_true",
                    help="every sprint whose status is 'active'")
    ap.add_argument("--stretch", action="store_true",
                    help="project stretch items too (default: backlog only)")
    ap.add_argument("--apply", action="store_true", help="write; default is a dry run")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any sprint item is underspecified")
    args = ap.parse_args()

    paths = pick(args.space, args.sprint, args.active)
    if not paths:
        print("no sprint selected — pass --sprint <id> or mark one status: active")
        return 0

    from org_workspace import OrgWorkspace

    org_dir = REPO / args.space / "org"
    org_files = [p for p in (org_dir / "next_actions.org", org_dir / "inbox.org",
                             org_dir / "someday.org") if p.exists()]
    ws = OrgWorkspace()
    for f in org_files:
        ws.load(f)

    target = org_dir / "next_actions.org"
    keep_ids: set[str] = set()
    created, updated, problems = [], [], []

    for sp in paths:
        sprint = yaml.safe_load(sp.read_text()) or {}
        sid = sprint.get("sprint_id") or sp.parent.name
        mine, probs = agent_items(sprint, args.stretch)
        problems += [f"{sid}: {p}" for p in probs]

        for it in mine:
            tid = f"org-sprint-{slug(sid)}-{slug(it['id'])}"
            keep_ids.add(tid)
            tags = ["AI"] + [slug(t) for t in (it.get("labels") or [])]
            props = {
                "ID": tid,
                "ROADMAP": str(it["roadmap"]),
                "MILESTONE": str(it["milestone"]),
                "SURFACE": str(it["surface"]),
                "DONE_WHEN": one_line(it["acceptance"]),
                "SPRINT": sid,
                "SPRINT_ITEM": str(it["id"]),
                "ASSIGNEE": str(it.get("default_claimer") or ""),
                "SOURCE": "sprint_sync",
                "REF": str(it.get("ref") or ""),
            }
            if it.get("effort_estimate"):
                props["EFFORT"] = str(it["effort_estimate"])

            node = ws.find_by_id(tid)
            if node is None:
                if args.apply:
                    ws.create_node(
                        file=target,
                        heading=str(it["title"]),
                        state="TODO",
                        tags=tags,
                        body=one_line(it["acceptance"]),
                        **props,
                    )
                created.append((tid, it["title"]))
            else:
                changed = []
                for k, v in props.items():
                    if k == "ID":
                        continue
                    if (node.properties or {}).get(k) != v:
                        changed.append(k)
                        if args.apply:
                            ws.set_property(node, k, v)
                cur = set(node.shallow_tags or [])
                if "AI" not in cur:
                    changed.append("tags")
                    if args.apply:
                        ws.set_tags(node, sorted(cur | {"AI"}))
                if changed:
                    updated.append((tid, it["title"], changed))

    # ---- everything not in the sprint loses the tag ------------------------
    cleared, held, stuck = [], [], []
    for node in ws.all_nodes():
        if node.todo not in ("TODO", "NEXT"):
            continue
        # shallow_tags: only a tag on this heading's own line is queued, and
        # only that one can be removed here. An :AI: inherited from an ancestor
        # is invisible to nightshift anyway (it parses the heading line, never
        # the parents), so treating it as queued would have this script report
        # un-queuing work it did not touch and could not have. It did exactly
        # that for four tasks before this was corrected on 2026-09-04.
        tags = list(node.shallow_tags or [])
        if "AI" not in tags:
            continue
        nid = (node.properties or {}).get("ID")
        if nid in keep_ids:
            continue
        props = node.properties or {}
        status = str(props.get("NIGHTSHIFT_STATUS") or "").lower()
        if status in IN_FLIGHT:
            age = _age_days(props)
            if age is None or age <= STALE_AFTER_DAYS:
                held.append((nid, node.heading[:60], status))
                continue
            stuck.append((nid, node.heading[:60], status, age))
            # falls through and is un-queued
        if args.apply:
            ws.set_tags(node, [t for t in tags if t != "AI"])
        cleared.append((nid, node.heading[:60]))

    if args.apply:
        ws.save_all()

    mode = "APPLIED" if args.apply else "DRY RUN — nothing written"
    print(f"sprint_sync — {mode}")
    print(f"sprints: {', '.join(p.parent.name for p in paths)}\n")
    print(f"queued (created)   {len(created)}")
    for tid, title in created[:12]:
        print(f"   + {title[:70]}")
    print(f"queued (updated)   {len(updated)}")
    for tid, title, ch in updated[:12]:
        print(f"   ~ {title[:52]} [{', '.join(ch[:4])}]")
    print(f"un-queued (:AI: removed, task kept)  {len(cleared)}")
    for nid, h in cleared[:12]:
        print(f"   - {h}")
    if len(cleared) > 12:
        print(f"   ...and {len(cleared) - 12} more")
    if held:
        print(f"held in flight (not touched)  {len(held)}")
        for nid, h, s in held[:6]:
            print(f"   = {h} [{s}]")
    if stuck:
        print(f"\nSTUCK — claimed to be running, has not moved  {len(stuck)}")
        for nid, h, s, age in stuck:
            print(f"   ! {h}")
            print(f"     {s} for {age:.0f} days — un-queued; nothing is running it")
    if problems:
        print(f"\nREFUSED — underspecified sprint items  {len(problems)}")
        for p in problems:
            print(f"   ! {p}")
        print("   fix these in sprint.yaml; an agent cannot act on them as written")

    return 1 if (args.strict and problems) else 0


if __name__ == "__main__":
    sys.exit(main())
