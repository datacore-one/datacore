#!/usr/bin/env python3
"""Validate 5-plur/roadmap.yaml against its spec.

The roadmap's forcing function is that every item serves an intent. This makes
it mechanical: a `serves` entry that does not resolve to an INTENT_ID in
org/intents.org is an error, not a warning. Per the spec, unknown keys are an
error too — silent acceptance is how parallel tracking systems grow.

Companion to roadmap_align.py, which maps org tasks and GitHub issues onto the
same graph. This one checks the roadmap file itself.

Usage:
    python3 .datacore/lib/roadmap_validate.py [--file 5-plur/roadmap.yaml]
    python3 .datacore/lib/roadmap_validate.py --query blocked_on=human
    python3 .datacore/lib/roadmap_validate.py --query horizon=now
    python3 .datacore/lib/roadmap_validate.py --coverage
"""

import argparse
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("pyyaml required: pip install pyyaml")

REPO = Path(__file__).resolve().parents[2]
DEFAULT_ROADMAP = REPO / "5-plur" / "roadmap.yaml"
INTENTS_ORG = REPO / "5-plur" / "org" / "intents.org"

ITEM_KEYS = {
    "id", "track", "title", "outcome", "serves", "drive", "horizon", "status",
    "blocked_on", "delegable", "owner", "gh", "org", "unblocks", "shipped",
    "gate", "note", "also", "embargoed", "hypothesis",
}
REQUIRED = {"id", "track", "title", "outcome", "serves", "horizon", "status", "shipped"}

HORIZONS = {"now", "next", "later", "gated"}
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default=str(DEFAULT_ROADMAP))
    ap.add_argument("--query", help="field=value, e.g. blocked_on=human")
    ap.add_argument("--coverage", action="store_true", help="intent coverage + orphan intents")
    args = ap.parse_args()

    roadmap = yaml.safe_load(Path(args.file).read_text())
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

    errors = validate(roadmap, intent_ids)
    hard = [e for e in errors if not e.startswith("NOTE")]
    for e in errors:
        print(("NOTE  " if e.startswith("NOTE") else "ERROR ") + e.removeprefix("NOTE "))
    print()
    print(f"{len(items)} items, {len(hard)} error(s)")
    return 1 if hard else 0


if __name__ == "__main__":
    sys.exit(main())
