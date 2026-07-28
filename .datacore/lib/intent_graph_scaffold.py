#!/usr/bin/env python3
"""Seed a per-space intent graph from what the space already declares.

Why per-space
-------------
One graph could only ever describe one venture. The existing graph describes
Datacore, so 154 open tasks across Datafund, FDS, Forge, Meridian, Megaphone
and personal had no branch to hang from — not because that work is unimportant
but because the structure had no room for it.

Each space owns its own `intents.org`. The personal view is their union: the
principal works across all of them, so the composition is the whole point, not
an afterthought. Ids are namespaced by space (`5-plur:north-star`) so two
ventures can both have a "growth" node without colliding, and `:SERVES:` can
still cross spaces when one venture's work genuinely serves another's goal.

Seeded, not invented
--------------------
Every space already states its strategy in `venture.yaml` — a thesis, a north
star, positioning, and roles with cadences. This turns those declarations into
graph nodes so the file starts as an editable draft of what the space already
says about itself. It does NOT invent goals: nodes are marked DRAFT and carry
no success criteria, because those are the principal's to write and a
plausible-looking invented metric is worse than a visible blank.

    python3 intent_graph_scaffold.py --dry-run
    python3 intent_graph_scaffold.py --space 5-plur
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import yaml
except ImportError:
    yaml = None


def slug(text: str, taken: set[str]) -> str:
    stop = {"the", "a", "an", "is", "are", "of", "for", "to", "and", "in", "on"}
    words = [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in stop][:4]
    base = "-".join(words) or "node"
    out, n = base, 2
    while out in taken:
        out, n = f"{base}-{n}", n + 1
    taken.add(out)
    return out


def render(space: str, v: dict) -> str:
    taken: set[str] = set()
    name = v.get("display_name") or v.get("name") or space
    thesis = (v.get("thesis") or "").strip().splitlines()
    vision = thesis[0].strip() if thesis else f"{name} — vision not yet stated"
    north = str(v.get("north_star") or "").strip()
    stage = str(v.get("stage") or "").strip()

    L = [
        f"#+TITLE: {name} — Intent Graph",
        "#+CATEGORY: intent",
        "#+STARTUP: overview",
        "",
        f"# Seeded by .datacore/lib/intent_graph_scaffold.py from {space}/venture.yaml.",
        "# Heading depth is the graph level; a child obviously serves its parent.",
        "# :SERVES: adds cross-branch parents and MAY point into another space",
        "# (e.g. 5-plur:knowledge-exchange) — that is how ventures that serve",
        "# each other stay visible as one graph under the personal view.",
        "#",
        "# Nodes marked DRAFT were derived from venture.yaml, not authored. Goals",
        "# deliberately carry no :SUCCESS: — an invented metric reads as real and",
        "# is worse than a visible blank. Add them, then drop the DRAFT tag.",
        "",
        f"* {vision}  :vision:",
        "  :PROPERTIES:",
        f"  :INTENT_ID: {slug('vision ' + name, taken)}",
        f"  :SPACE: {space}",
        f"  :STAGE: {stage or 'unstated'}",
        "  :END:",
        "",
    ]
    if north:
        L += [
            f"** North star: {north}  :intent:DRAFT:",
            "   :PROPERTIES:",
            f"   :INTENT_ID: {slug('north star ' + north, taken)}",
            "   :LEVEL: 1",
            f"   :METRIC: {north}",
            "   :END:",
            "",
            "   The one number this venture is trying to move. Goals beneath it",
            "   should each state how they move it.",
            "",
        ]

    # Roles are the venture's own division of work, so they are the most
    # honest seed for level-1 branches: each already owns a set of cadences.
    roles = v.get("roles") or {}
    for role, spec in roles.items():
        desc = str((spec or {}).get("description") or role).strip()
        L += [
            f"** {role.upper()}: {desc}  :intent:DRAFT:",
            "   :PROPERTIES:",
            f"   :INTENT_ID: {slug(role + ' ' + desc, taken)}",
            "   :LEVEL: 1",
            f"   :ROLE: {role}",
            "   :END:",
            "",
        ]
        cadences = (spec or {}).get("cadences") or {}
        for freq, items in cadences.items():
            for item in items or []:
                L += [
                    f"*** {item}  :goal:DRAFT:",
                    "    :PROPERTIES:",
                    f"    :INTENT_ID: {slug(str(item), taken)}",
                    "    :LEVEL: 2",
                    f"    :CADENCE: {role}.{item}",
                    f"    :FREQUENCY: {freq}",
                    "    :END:",
                ]
        if cadences:
            L.append("")
    return "\n".join(L).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--space", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing intents.org (default: skip)")
    a = ap.parse_args()
    root = Path(a.root).expanduser()
    if not yaml:
        print("pyyaml required", file=sys.stderr)
        return 1

    targets = sorted(root.glob("[0-9]-*/venture.yaml"))
    if a.space:
        targets = [t for t in targets if t.parent.name == a.space]
    written = skipped = 0
    for f in targets:
        space = f.parent.name
        # DIP-0015: a space root allows only the listed dirs; org files
        # live in org/, which is also where the tasks they govern live.
        dest = f.parent / "org" / "intents.org"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and not a.force:
            print(f"  skip   {space:14} intents.org exists")
            skipped += 1
            continue
        try:
            v = yaml.safe_load(f.read_text()) or {}
        except Exception as e:
            print(f"  ERROR  {space:14} {e}")
            continue
        text = render(space, v)
        n_nodes = text.count("\n*")
        if a.dry_run:
            print(f"  would  {space:14} {n_nodes} nodes")
        else:
            dest.write_text(text)
            print(f"  wrote  {space:14} {dest.relative_to(root)}  ({n_nodes} nodes)")
        written += 1
    print(f"\n  {written} seeded, {skipped} skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
