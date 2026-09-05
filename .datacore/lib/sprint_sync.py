#!/usr/bin/env python3
"""Project a sprint into the agent queue, and keep the queue equal to the sprint.

THE PROBLEM THIS SOLVES. `sprint.yaml` was written, reviewed and never consumed:
nothing in the tree read it, and `miles_routing.sprint_tag_filter` had zero
readers anywhere.

THERE ARE THREE LEVELS, AND ONLY THE THIRD RUNS. Getting this wrong is the
single most expensive mistake available here, so it is spelled out:

  1. A task in next_actions.org            — exists
  2. ...tagged :AI:                        — is a CANDIDATE
  3. ...AND referenced from nightshift.org — RUNS

`task_queue.build_queue` keeps only entries whose file is `nightshift.org` and
drops everything else. On 2026-09-04 the fleet had 261 tasks at level 2 and the
runner executed 2 — the system already said so on every run ("261 :AI: task(s)
tagged but not queued — they will NOT run") and it read as noise. 5-plur had no
nightshift.org at all, so a perfectly specified sprint task would have sat at
level 2 forever. This script writes all three levels.

THE FIX IS A DIRECTION, NOT A SYNC. `sprint.yaml` is the source; both the tag
and the queue entry are projections of it:

    in sprint + claimed by an agent   ->  task tagged :AI:, entry in the queue
    not in sprint                     ->  :AI: removed, queue entry removed
                                          (the task itself always stays)

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

# DIP-0009 v2.0 closed states. A task in one of these is finished; the sprint
# still lists the item, so the queue must check the TASK, not the sprint.
CLOSED_STATES = {"DONE", "DEFERRED", "CANCELLED"}


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")


def tag_slug(s: str) -> str:
    """An org TAG, which may not contain a hyphen.

    nightshift_parser matches tags as `(\\s+:[\\w:]+:)?$` and `\\w` excludes
    `-`. A heading ending `:AI:release-blocker:pinned:` therefore matches NO
    tag group at all: the whole string stays inside the title, the task parses
    with zero tags, and it is silently never selected. A sprint label of
    "release-blocker" put exactly one item in that state — queued, well
    specified, and invisible to the runner. Underscores parse; hyphens do not.
    """
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def one_line(s: str) -> str:
    """Acceptance prose -> a single-line DONE_WHEN an org property can hold.

    Rejoin words the sprint's own line-wrapping split across a hyphen first:
    YAML folds "thirty-\\neight" and a naive whitespace collapse turns that
    into "thirty- eight". Only rejoin when the next character is lowercase, so
    genuine compounds ("self-hosted", "no-benchmark-war") keep their hyphen.
    """
    s = re.sub(r"-\n(?=[a-z])", "", (s or ""))
    return re.sub(r"\s+", " ", s.strip())


QUEUE_HEADER = """#+TITLE: Nightshift Queue — {space}
#+CATEGORY: Nightshift
#+FILETAGS: :nightshift:AI:
#+SEQ_TODO: TODO(t) NEXT(n!) WAITING(w!) REVIEW(r!) | DONE(d!) DEFERRED(f!) CANCELLED(c!)
#+STARTUP: overview

# GENERATED SECTION BELOW — written by .datacore/lib/sprint_sync.py.
# The runner executes entries from THIS FILE ONLY (task_queue.build_queue
# keeps `nightshift.org` entries and drops everything else), so a task tagged
# :AI: in next_actions.org is a CANDIDATE and nothing more. That distinction
# cost this sprint its whole premise once already: five perfectly specified
# sprint tasks sat in next_actions.org where no runner would ever have seen
# them. Entries here are REFERENCES — SOURCE_ID points at the real task, so
# the spec cannot drift from the copy that runs.

* Sprint queue
  :PROPERTIES:
  :ID: org-sprint-queue-{slug}
  :END:
"""


def sync_queue(ws, space: str, wanted: list[dict], apply: bool,
               seen_sprints: set[str] | None = None) -> tuple[list, list]:
    """Mirror the sprint's agent tasks as reference entries in nightshift.org.

    Returns (added, removed). Idempotent: an entry already present is left
    alone, and an entry whose task is no longer in the sprint is removed.
    """
    qfile = REPO / space / "org" / "nightshift.org"
    if not qfile.exists():
        if not apply:
            return [(w["id"], w["title"], "would create " + str(qfile)) for w in wanted], []
        qfile.parent.mkdir(parents=True, exist_ok=True)
        qfile.write_text(QUEUE_HEADER.format(space=space, slug=slug(space)))
    ws.load(qfile)

    want_by_qid = {f"{w['id']}-q": w for w in wanted}
    have = {}
    for n in ws.all_nodes():
        if "nightshift.org" not in str(n.path):  # NodeView exposes .path, not .file_path
            continue
        # Identify our own entries by their PROPERTIES, not by an id prefix.
        # The prefix test only held while every projected task was created by
        # this script; an ADOPTED task keeps its author's id, so
        # `org-20260904-trust-surface-q` failed the prefix check and the entry
        # would have been recreated on every single run.
        pr = n.properties or {}
        nid = pr.get("ID") or ""
        if pr.get("SOURCE_ID") and pr.get("SPRINT") and nid.endswith("-q"):
            have[nid] = n

    added, removed, skipped_foreign = [], [], []
    for qid, w in want_by_qid.items():
        if qid in have:
            continue
        added.append((qid, w["title"], "queued"))
        if apply:
            ws.create_node(
                file=qfile,
                heading=w["title"],
                state="NEXT",
                tags=["AI"] + w["tags"],
                body=f"Reference entry. The specification lives on {w['id']}.",
                ID=qid,
                SOURCE_ID=w["id"],
                SPACE=space,
                SPRINT=w["sprint"],
                ASSIGNEE=w["assignee"],
                SURFACE=w["surface"],
            )
    for qid, node in have.items():
        if qid in want_by_qid:
            continue
        # ONLY remove an entry whose owning sprint we actually discovered.
        #
        # Absence of a sprint file is not absence of a sprint. On the nightshift
        # host `2-projects/plur/sprints/` does not exist, so this script sees
        # one sprint where the Mac sees four — and the old logic read "not in
        # any sprint I found" as "not in any sprint", which would have deleted
        # 22 live queue entries on the 20:04 run. A checkout that cannot see a
        # sprint has no standing to judge its work.
        owner = (node.properties or {}).get("SPRINT")
        if seen_sprints is not None and owner and owner not in seen_sprints:
            skipped_foreign.append((qid, node.heading[:52], owner))
            continue
        removed.append((qid, node.heading[:58]))
        if apply:
            ws.remove_node(node)
    return added, removed, skipped_foreign


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
    """Every sprint.yaml under the space, ONE per sprint_id.

    Worktrees duplicate the whole tree, so `2-projects/*/sprints/*` finds the
    same sprint once per checkout — `enterprise` and `enterprise-wt-ci-gate`
    both matched, and every sprint appeared twice. Projecting a sprint twice is
    harmless only by luck; deduplicate on the directory name, which is the
    sprint id, and prefer the non-worktree path.
    """
    found = sorted((REPO / space).glob("2-projects/*/sprints/*/sprint.yaml"))
    # Track-level sprints too. Work that is not in one code repo — ops, hub
    # submissions, bizdev, statutory — has no 2-projects/ home, and filing it
    # under a code repo's sprints/ would be a lie about where it lives.
    # It goes under `1-tracks/<track>/sprints/` rather than a bare `sprints/`
    # at the space root: DIP-0015 fixes the allowed root directories and the
    # structure hook refuses a new one, correctly.
    found += sorted((REPO / space).glob("1-tracks/*/sprints/*/sprint.yaml"))
    best: dict[str, Path] = {}
    for p in found:
        key = p.parent.name
        prev = best.get(key)
        if prev is None or ("-wt-" in str(prev) and "-wt-" not in str(p)):
            best[key] = p
    return sorted(best.values())


def pick(space: str, want: str | None, active: bool) -> list[Path]:
    found = discover(space)
    if want:
        hit = [p for p in found if want in str(p)]
        if not hit:
            sys.exit(f"no sprint matching {want!r} under {space}/2-projects/*/sprints/")
        return hit
    if active:
        # `status: active` alone is not enough. Ten sprints from W23 to W31
        # still said active on 2026-09-04 — months of sprints nobody closed —
        # and --active --apply would have projected every one of them into
        # tonight's queue. A sprint whose end date has passed is over whatever
        # its status field says; the field is a claim, the date is a fact.
        from datetime import date

        today = date.today()
        out, expired = [], []
        for p in found:
            d = yaml.safe_load(p.read_text()) or {}
            if d.get("status") != "active":
                continue
            end = (d.get("dates") or {}).get("end")
            if isinstance(end, str):
                try:
                    end = date.fromisoformat(end)
                except ValueError:
                    end = None
            if end and end < today:
                expired.append((p.parent.name, end))
                continue
            out.append(p)
        if expired:
            print(f"IGNORING {len(expired)} sprint(s) still marked active whose "
                  f"end date has passed — close them:")
            for name, end in sorted(expired, key=lambda x: str(x[1])):
                print(f"   {name} ended {end} ({(today - end).days} days ago)")
            print()
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
        # An adopting item need only name the task; the task already carries
        # roadmap, surface and its definition of done, and repeating them in the
        # sprint file creates two copies that drift.
        required = ("title", "org") if it.get("org") else REQUIRED
        missing = [f for f in required if not it.get(f)]
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
    queue_want: list[dict] = []
    seen_sprints: set[str] = set()

    for sp in paths:
        sprint = yaml.safe_load(sp.read_text()) or {}
        sid = sprint.get("sprint_id") or sp.parent.name
        seen_sprints.add(sid)
        mine, probs = agent_items(sprint, args.stretch)
        problems += [f"{sid}: {p}" for p in probs]

        for it in mine:
            # ADOPT vs CREATE. `org: <task-id>` means the task ALREADY EXISTS —
            # someone wrote it, with its own ROADMAP/SURFACE/DONE_WHEN — and the
            # sprint is claiming it, not re-describing it. Without this the only
            # way to sprint an existing task was to create a second one beside
            # it, which then competes with the original and strips the
            # original's tag on the same pass. A parallel session hit exactly
            # that on 2026-09-04 with thirteen roadmap-unblocking tasks.
            adopted = str(it.get("org") or "").strip()
            tid = adopted or f"org-sprint-{slug(sid)}-{slug(it['id'])}"
            keep_ids.add(tid)
            tags = ["AI"] + [tag_slug(t) for t in (it.get("labels") or [])]
            props = {
                "ID": tid,
                "ROADMAP": str(it.get("roadmap") or ""),
                "MILESTONE": str(it.get("milestone") or ""),
                "SURFACE": str(it.get("surface") or ""),
                "DONE_WHEN": one_line(it.get("acceptance") or ""),
                "SPRINT": sid,
                "SPRINT_ITEM": str(it["id"]),
                "ASSIGNEE": str(it.get("default_claimer") or ""),
                "SOURCE": "sprint_sync",
                "REF": str(it.get("ref") or ""),
            }
            if it.get("effort_estimate"):
                props["EFFORT"] = str(it["effort_estimate"])

            if adopted:
                existing = ws.find_by_id(adopted)
                if existing is None:
                    problems.append(
                        f"{sid}: {it['id']} adopts org task {adopted!r}, which does "
                        f"not exist — refusing rather than silently creating a "
                        f"second task under that name")
                    keep_ids.discard(tid)
                    continue
                # The author's own words win. The sprint supplies only what the
                # task is missing, plus its own SPRINT bookkeeping — overwriting
                # a DONE_WHEN somebody wrote with one paraphrased into a sprint
                # file is how the definition of done quietly drifts.
                have = existing.properties or {}
                for k in ("ROADMAP", "MILESTONE", "SURFACE", "DONE_WHEN", "REF",
                          "EFFORT", "ASSIGNEE"):
                    if str(have.get(k) or "").strip():
                        props[k] = have[k]
                gaps = [k for k in ("ROADMAP", "SURFACE", "DONE_WHEN")
                        if not str(props.get(k) or "").strip()]
                if gaps:
                    problems.append(
                        f"{sid}: {it['id']} adopts {adopted} but neither the "
                        f"sprint nor the task supplies {', '.join(gaps)} — an "
                        f"agent still could not run it")
                    keep_ids.discard(tid)
                    continue

            node = ws.find_by_id(tid)

            # A finished item is not re-queued. Without this the sync would
            # resurrect its own completed work every run: the queue entry is
            # keyed off the sprint item, not the task's state, so S02-5 —
            # already DONE — was about to be handed back to an agent.
            if node is None or node.todo not in CLOSED_STATES:
                queue_want.append({
                    "id": tid,
                    "title": str(it["title"]),
                    "tags": [tag_slug(t) for t in (it.get("labels") or [])],
                    "sprint": sid,
                    "assignee": str(it.get("default_claimer") or ""),
                    "surface": str(props.get("SURFACE") or ""),
                })

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
                # Normalise the WHOLE tag set, not just "is AI present". An
                # earlier version only added the AI tag and left everything
                # else alone, so a task that already carried a hyphenated
                # label kept it — and a hyphen makes nightshift's tag regex
                # miss the group entirely, giving the task zero tags and
                # hiding it from selection. Adding AI to a set the parser
                # cannot read achieves nothing.
                cur = set(node.shallow_tags or [])
                want_tags = set(tags)
                if not want_tags.issubset(cur) or any("-" in t for t in cur):
                    changed.append("tags")
                    if args.apply:
                        keep = {t for t in cur if "-" not in t}
                        ws.set_tags(node, sorted(keep | want_tags))
                if changed:
                    updated.append((tid, it["title"], changed))

    # ---- everything not in the sprint loses the tag ------------------------
    cleared, held, stuck, foreign = [], [], [], []
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
        # A task belonging to a sprint this checkout cannot see is not ours to
        # un-queue — same reasoning as the queue guard above.
        owner = props.get("SPRINT")
        if owner and owner not in seen_sprints:
            foreign.append((nid, node.heading[:52], owner))
            continue
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

    # The queue is a different file from the pool, and only the queue runs.
    q_added, q_removed, q_foreign = sync_queue(
        ws, args.space, queue_want, args.apply, seen_sprints=seen_sprints)

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
    print(f"nightshift.org queue entries added   {len(q_added)}")
    for qid, title, note in q_added[:12]:
        print(f"   > {title[:62]}  [{note}]")
    if q_removed:
        print(f"nightshift.org queue entries removed {len(q_removed)}")
        for qid, h in q_removed[:8]:
            print(f"   < {h}")
    print(f"un-queued (:AI: removed, task kept)  {len(cleared)}")
    for nid, h in cleared[:12]:
        print(f"   - {h}")
    if len(cleared) > 12:
        print(f"   ...and {len(cleared) - 12} more")
    if held:
        print(f"held in flight (not touched)  {len(held)}")
        for nid, h, s in held[:6]:
            print(f"   = {h} [{s}]")
    if foreign or q_foreign:
        n = len(foreign) + len(q_foreign)
        print(f"\nLEFT ALONE — {n} item(s) belong to a sprint this checkout "
              f"cannot see; absence of a sprint file is not absence of a sprint")
        for nid, h, owner in (foreign + q_foreign)[:6]:
            print(f"   ~ {h}  [{owner}]")
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
