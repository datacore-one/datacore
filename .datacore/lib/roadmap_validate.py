#!/usr/bin/env python3
"""Validate a space's roadmap.yaml against its spec (default space: 5-plur).

The roadmap's forcing function is that every item serves an intent. This makes
it mechanical: a `serves` entry that does not resolve to an INTENT_ID in
org/intents.org is an error, not a warning. Per the spec, unknown keys are an
error too — silent acceptance is how parallel tracking systems grow.

Companion to roadmap_align.py, which maps org tasks and GitHub issues onto the
same graph. This one checks the roadmap file itself.

Usage:
    python3 .datacore/lib/roadmap_validate.py [--space 2-datacore] [--file roadmap.yaml]
    python3 .datacore/lib/roadmap_validate.py --query blocked_on=human
    python3 .datacore/lib/roadmap_validate.py --query horizon=now
    python3 .datacore/lib/roadmap_validate.py --coverage
"""

import argparse
import os
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip install pyyaml")

REPO = Path(__file__).resolve().parents[2]

# One roadmap per space, one set of tools. Every path below derives from the
# selected space, so a second roadmap can never validate against the first
# one's intent graph by accident. Select with --space or ROADMAP_SPACE.
SPACE = os.environ.get("ROADMAP_SPACE", "5-plur")


def space_root(space: str) -> Path:
    """A space is a directory name under the Data root, or an absolute path."""
    p = Path(space).expanduser()
    return p if p.is_absolute() else REPO / space


def configure(space: str) -> None:
    """Point every path at `space`. Runs at import and again for --space."""
    global SPACE, SPACE_ROOT, SPACE_TAG, DEFAULT_ROADMAP, INTENTS_ORG, SPACE_ORG, ORG_FILES
    SPACE = space
    SPACE_ROOT = space_root(space)
    name = SPACE_ROOT.name
    # `5-plur` -> `plur`: the tag a task in the personal space carries when
    # it belongs to this space.
    SPACE_TAG = name.split("-", 1)[1] if "-" in name and name[0].isdigit() else name
    DEFAULT_ROADMAP = SPACE_ROOT / "roadmap.yaml"
    INTENTS_ORG = SPACE_ROOT / "org" / "intents.org"
    SPACE_ORG = [str(SPACE_ROOT / "org" / f)
                 for f in ("next_actions.org", "someday.org", "inbox.org")]
    # The space's own org files, plus the personal space where a task for
    # this space is tagged with its short name.
    ORG_FILES = SPACE_ORG + ["0-personal/org/next_actions.org",
                             "0-personal/org/someday.org"]


configure(SPACE)

ITEM_KEYS = {
    "id", "track", "title", "outcome", "serves", "drive", "horizon", "status",
    "blocked_on", "delegable", "owner", "gh", "org", "unblocks", "shipped",
    "gate", "note", "also", "embargoed", "hypothesis", "lane", "rice",
    "rice_why", "goal", "compounds_for", "half", "rung", "milestone",
    "done_when",
}
REQUIRED = {"id", "track", "title", "outcome", "serves", "horizon", "status", "shipped"}

HORIZONS = {"now", "next", "later", "gated"}
# Every item names the rung it advances, or 'continuous' for work that runs
# alongside the ladder rather than gating a rung (ops, GEO, company, the merge
# queue). 'continuous' is a real answer, not a missing one — forcing ops work
# onto a product rung is what made the ladder meaningless the first time.
MILESTONES = {"M1", "M2", "M3", "M4", "M5", "continuous"}

# How an item proves it is done. The point of naming the KIND is that an agent
# can tell what artifact to go and produce, and a reviewer can tell what to go
# and look at — "done" stops being a judgement call and becomes a fetch.
#
#   test        a named test or suite passes that did not before
#   metric      a number crosses a stated threshold, measured by a named command
#   artifact    a file exists at a stated path with stated properties
#   screenshot  a visual state a human confirms — for UI and dashboards
#   url         a public address returns a stated response
#   merged-pr   named PRs are merged and their issues closed
#   decision    a decision is recorded in writing at a stated location
#   signed      a countersigned or legally executed document exists
EVIDENCE = {"test", "metric", "artifact", "screenshot", "url",
            "merged-pr", "decision", "signed"}
STATUSES = {"ready", "in_progress", "blocked", "done"}
BLOCKED_ON = {"human", "agent", "external", "dependency", "standing_block", None}
DRIVES = {"status", "self-protection", "affiliation", "disease-avoidance", "kin-care"}


# Only these node kinds are expected to carry roadmap items. A constraint is an
# anti-goal, a vision is not schedulable, and operations are recurring activity —
# reporting them as uncovered would bury the ones that matter.
ITEM_BEARING = {"goal", "intent", "initiative"}


def load_intents(path: Path) -> dict:
    """INTENT_ID -> node kind, taken from the heading tag above each ID."""
    if not path.exists():
        sys.exit(f"intents file not found: {path}")
    intents, kind = {}, None
    for line in path.read_text().splitlines():
        if line.startswith("*"):
            tags = re.findall(r":([a-z_]+):", line)
            kind = tags[-1] if tags else None
        m = re.match(r"^\s*:INTENT_ID:\s*(\S+)\s*$", line)
        if m:
            intents[m.group(1)] = kind
    return intents


def load_intent_ids(path: Path) -> set:
    return set(load_intents(path))


def validate(roadmap: dict, intent_ids: set) -> list:
    errors = []
    items = roadmap.get("items") or []
    if not items:
        errors.append("no items")
        return errors

    seen_ids = set()
    tracks = set(roadmap.get("tracks") or {})

    for i, item in enumerate(items):
        ref = item.get("id") or f"items[{i}]"

        # An epic on the near horizon with no definition of done cannot be
        # finished by anyone — human or agent — because nothing says what
        # finishing looks like. Later items are exempt: writing a condition for
        # work whose shape is unknown produces a fiction that has to be undone.
        if item.get("horizon") in ("now", "next") and not item.get("done_when"):
            errors.append(f"{ref}: horizon={item['horizon']} requires done_when — "
                          f"an epic nobody can check is not schedulable")

        # `blocked_on: human` says a person must act; `delegable: true` says an
        # agent may take it. Both at once tells an agent to start work it cannot
        # finish, which is the single most expensive contradiction on the board.
        if item.get("blocked_on") == "human" and item.get("delegable"):
            errors.append(f"{ref}: blocked_on=human contradicts delegable=true — "
                          f"an agent cannot finish work that waits on a person")

        dw = item.get("done_when")
        if dw is not None:
            if not isinstance(dw, dict):
                errors.append(f"{ref}: done_when must be a mapping")
            else:
                unk = set(dw) - {"condition", "evidence", "verify"}
                if unk:
                    errors.append(f"{ref}: done_when has unknown key(s) {sorted(unk)}")
                for k in ("condition", "evidence", "verify"):
                    if not dw.get(k):
                        errors.append(f"{ref}: done_when is missing {k!r} — "
                                      f"a condition nobody can check is not a definition of done")
                ev = dw.get("evidence")
                if ev and ev not in EVIDENCE:
                    errors.append(f"{ref}: done_when.evidence {ev!r} not in {sorted(EVIDENCE)}")
        ms = item.get("milestone")
        if ms is not None and ms not in MILESTONES:
            errors.append(f"{ref}: milestone {ms!r} not in {sorted(MILESTONES)}")
        unknown = set(item) - ITEM_KEYS
        if unknown:
            errors.append(f"{ref}: unknown key(s) {sorted(unknown)} — the spec makes this an error")

        for key in sorted(REQUIRED - set(item)):
            errors.append(f"{ref}: missing required field `{key}`")

        if item.get("id") in seen_ids:
            errors.append(f"{ref}: duplicate id")
        seen_ids.add(item.get("id"))

        if item.get("track") not in tracks:
            errors.append(f"{ref}: track `{item.get('track')}` is not declared in tracks:")

        for intent in item.get("serves") or []:
            if intent not in intent_ids:
                errors.append(f"{ref}: serves `{intent}` — no such INTENT_ID in intents.org")
        if not item.get("serves"):
            errors.append(f"{ref}: serves nothing — deletion candidate")

        h = item.get("horizon")
        if h not in HORIZONS:
            errors.append(f"{ref}: horizon `{h}` not in {sorted(HORIZONS)}")
        if h == "gated" and not item.get("gate"):
            errors.append(f"{ref}: horizon gated but no `gate` stated")
        if item.get("gate") and re.search(r"(?<![A-Za-z0-9-])20\d\d-\d\d-\d\d(?![A-Za-z0-9-])", str(item["gate"])):
            errors.append(f"{ref}: gate states a date — it must state a condition")

        if item.get("status") not in STATUSES:
            errors.append(f"{ref}: status `{item.get('status')}` not in {sorted(STATUSES)}")
        if item.get("blocked_on") not in BLOCKED_ON:
            errors.append(f"{ref}: blocked_on `{item.get('blocked_on')}` invalid")
        if item.get("status") == "blocked" and item.get("blocked_on") is None:
            errors.append(f"{ref}: status blocked but blocked_on is null")

        if item.get("drive") and item["drive"] not in DRIVES:
            errors.append(f"{ref}: drive `{item['drive']}` not in {sorted(DRIVES)}")

        if item.get("blocked_on") == "standing_block" and item.get("delegable"):
            errors.append(f"{ref}: standing_block must never be delegable")

        outcome, title = str(item.get("outcome", "")), str(item.get("title", ""))
        if outcome and outcome.strip().lower() == title.strip().lower():
            errors.append(f"{ref}: outcome restates the title — under-specified")

    for item in items:
        for dep in item.get("unblocks") or []:
            if dep not in seen_ids:
                errors.append(f"{item.get('id')}: unblocks `{dep}` — no such item")

    ns = roadmap.get("north_star") or {}
    if ns.get("status") == "OPEN":
        errors.append(
            "NOTE north_star.status is OPEN — every `horizon` in this file is "
            "provisional until it resolves (not a validation failure)"
        )
    return errors


# ORG_FILES is set by configure(): the space's org files plus 0-personal.

THEMES = {
    "packs / hub": r"\bpack|hub\b|marketplace|listing|seller",
    "provenance / audit": r"provenance|lineage|tamper|signed|attest|audit chain",
    "retrieval / recall": r"recall|retriev|rerank|embed|inject|bm25|vector|hybrid",
    "scopes / permissions": r"scope|permission|acl|multi-tenant|rbac|tenant",
    "enterprise delivery": r"enterprise|customer|deploy|onboard|install|docker|helm|runbook",
    "integrator / channel": r"integrator|channel|partner|reseller|civo|stackit",
    "geo / content": r"\bgeo\b|dev\.to|blog|share of voice|wikidata|seo|content|publish",
    "benchmark": r"benchmark|longmemeval|locomo|bench\b|leaderboard",
    "exchange / token": r"exchange|token|escrow|x402|verity|fee",
    "spec / standard": r"\bspec\b|standard|capsule|schema|protocol",
    "security / trust": r"security|vulnerab|trust page|soc2|dpa|secret|credential",
    "agents / nightshift": r"nightshift|agent fleet|miles|cadence|prompt|orchestrat",
    "verticals": r"vertical|clinical|medicine|health|legal|law",
    "fundraising": r"fundrais|investor|seed|deck|cap table|round",
    "infra / ops": r"\bci\b|workflow|backup|monitor|server|dns|smoke",
}


def report_orphans(roadmap):
    """PLUR tasks with no roadmap item, clustered.

    Most orphans are CORRECT — the roadmap is not a backlog. What this looks
    for is an orphan CLUSTER large enough to be outcome-level, which is the
    shape a missing roadmap item makes.
    """
    import json
    import subprocess
    from collections import defaultdict

    refs = set(re.findall(r"org-[0-9a-z-]{6,}", yaml.dump(roadmap)))
    rm = yaml.dump(roadmap).lower()
    buckets, total, covered = defaultdict(list), 0, 0
    for f in ORG_FILES:
        path = REPO / f
        if not path.exists():
            continue
        r = subprocess.run(["python3", str(REPO / ".datacore/lib/org_workspace_adapter.py"),
                            "list", "--file", str(path), "--states", "NEXT,TODO,WAITING,REVIEW"],
                           capture_output=True, text=True, cwd=REPO)
        if r.returncode:
            continue
        for task in json.loads(r.stdout)["tasks"]:
            tags = set(task.get("tags") or [])
            if not (SPACE_TAG in tags or f in SPACE_ORG
                    or SPACE_TAG in task["heading"].lower()):
                continue
            total += 1
            if (task.get("properties") or {}).get("ID") in refs:
                covered += 1
                continue
            hits = [k for k, pat in THEMES.items()
                    if re.search(pat, task["heading"], re.I)] or ["(unthemed)"]
            for k in hits:
                buckets[k].append(task["heading"])

    print(f"{total} {SPACE_TAG.upper()}-scoped open tasks · {covered} referenced by a roadmap item "
          f"· {total - covered} orphaned\n")
    print("Most orphans are correct — the roadmap is not a backlog. Look for a\n"
          "cluster big enough to be outcome-level with no theme in the file.\n")
    for k, v in sorted(buckets.items(), key=lambda x: -len(x[1])):
        probe = k.split(" /")[0].split()[0].lower()
        flag = "" if probe in rm else "   <- NO ROADMAP ITEM ON THIS THEME"
        print(f"{len(v):5}  {k}{flag}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default=SPACE,
                    help="space holding roadmap.yaml and org/intents.org: a "
                         "directory under the Data root, or an absolute path")
    ap.add_argument("--file", default=None,
                    help="roadmap file (default: <space>/roadmap.yaml)")
    ap.add_argument("--query", help="field=value, e.g. blocked_on=human")
    ap.add_argument("--coverage", action="store_true", help="intent coverage + orphan intents")
    ap.add_argument("--orphans", action="store_true",
                    help="PLUR org tasks with no roadmap item, clustered by theme")
    args = ap.parse_args()

    configure(args.space)
    roadmap = yaml.safe_load(Path(args.file or DEFAULT_ROADMAP).read_text())
    intent_ids = load_intent_ids(INTENTS_ORG)
    items = roadmap.get("items") or []

    if args.query:
        field, _, value = args.query.partition("=")
        want = {"true": True, "false": False, "null": None}.get(value.lower(), value)
        hits = [i for i in items if i.get(field) == want]
        print(f"{len(hits)} item(s) where {field} == {value}\n")
        for i in hits:
            print(f"  {i['id']}  [{i.get('track')}]  {i['title']}")
            print(f"      owner={i.get('owner')}  status={i.get('status')}  horizon={i.get('horizon')}")
        return 0

    if args.coverage:
        intents = load_intents(INTENTS_ORG)
        served = {s for i in items for s in (i.get("serves") or [])}
        bearing = {k for k, v in intents.items() if v in ITEM_BEARING}
        covered = served & bearing
        print(f"item-bearing intents: {len(bearing)}   covered: {len(covered)}   "
              f"({100 * len(covered) // max(len(bearing), 1)}%)\n")
        orphans = sorted(bearing - served)
        if orphans:
            print("GOALS WITH NO ROADMAP ITEM — each is a gap or a deletion candidate:")
            for intent in orphans:
                print(f"  - {intent}  ({intents[intent]})")
        else:
            print("Every item-bearing intent has at least one roadmap item.")
        skipped = sorted(set(intents) - bearing - served)
        print(f"\nnot expected to carry items ({len(skipped)}): "
              + ", ".join(f"{i}/{intents[i]}" for i in skipped))
        return 0

    if args.orphans:
        return report_orphans(roadmap)

    errors = validate(roadmap, intent_ids)
    hard = [e for e in errors if not e.startswith("NOTE")]
    for e in errors:
        print(("NOTE  " if e.startswith("NOTE") else "ERROR ") + e.removeprefix("NOTE "))
    print()
    print(f"{len(items)} items, {len(hard)} error(s)")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
