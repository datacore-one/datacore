#!/usr/bin/env python3
"""Write a venture's first roadmap.yaml — one that passes its own validator.

WHY. roadmap_validate, roadmap_render and roadmap_drift all take --space and all
assume the file already exists. Nothing created it, and the rules it must satisfy
lived only inside the validator's Python. So the first roadmap for a new venture
was written by copying 5-plur's 4,320-line file and deleting things, which
reintroduces exactly the drift those three tools exist to catch.

Seeds from what the space already declares — venture.yaml's north_star and
thesis, and the INTENT_IDs in org/intents.org — because a roadmap whose items
serve no intent is a roadmap the validator will reject.

    python3 roadmap_init.py --space 9-practice --dry-run
    python3 roadmap_init.py --space 9-practice
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]

HEADER = """\
# {name} roadmap — the source of truth.
#
# Every human-readable roadmap is GENERATED from this file:
#   python3 .datacore/lib/roadmap_render.py --space {space} --html out.html
#
# THE RULES, enforced by roadmap_validate.py --space {space}:
#   - Outcome-level only, 30-50 items. Not a backlog: tasks and GitHub issues
#     stay where they are and link upward.
#   - Every item MUST `serves` at least one INTENT_ID from org/intents.org.
#     An unresolvable reference is an error; an item serving nothing is a
#     deletion candidate.
#   - `gate` states a CONDITION, never a date. `horizon: gated` is not
#     scheduled and must not be picked up by sprint planning.
#   - `blocked_on: standing_block` is never selected by sprint planning.
#   - `shipped: false` is required on anything a public claim could rest on.
#   - `outcome` is what changes in the world, not what gets built.
#   - Unknown keys are an error. Silent acceptance is how parallel tracking
#     systems grow.
#
# Check it:  python3 .datacore/lib/roadmap_validate.py --space {space}
# Drift:     python3 .datacore/lib/roadmap_drift.py --space {space} --strict
# Agents:    python3 .datacore/lib/agent_readiness.py --space {space}
"""


def intents_of(space_dir: Path) -> list[str]:
    f = space_dir / "org" / "intents.org"
    if not f.exists():
        return []
    return re.findall(r"^\s*:INTENT_ID:\s*(\S+)", f.read_text(errors="ignore"), re.M)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", required=True)
    ap.add_argument("--root", default=str(REPO))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    space_dir = Path(a.root) / a.space
    if not space_dir.is_dir():
        sys.exit(f"no such space: {space_dir}")
    target = space_dir / "roadmap.yaml"
    if target.exists() and not a.force:
        sys.exit(f"{target} exists — pass --force to replace it")

    vy = space_dir / "venture.yaml"
    v = yaml.safe_load(vy.read_text()) if vy.exists() else {}
    name = v.get("display_name") or v.get("name") or a.space
    north = str(v.get("north_star") or "").strip()
    intents = intents_of(space_dir)

    doc: dict = {
        "version": 2,
        "updated": date.today().isoformat(),
        "goals": [{
            "id": "G1",
            "goal": north or "STATE THE ONE OUTCOME THIS VENTURE IS FOR",
            "from": "where it starts today — so the distance is visible without a paragraph",
            "next": "the next thing that would move it",
            "serves": intents[:2] or ["REPLACE-WITH-AN-INTENT_ID-FROM-org/intents.org"],
            "why_this_number": "why this target and not a larger or smaller one",
        }],
        "north_star": {"metric": north or "UNSET", "target": None, "by": None,
                       "source": f"seeded by roadmap_init {date.today().isoformat()}",
                       "status": "UNSET" if not north else "SET"},
        # Deliberately empty. An invented item reads as a real commitment, and a
        # roadmap seeded with plausible-looking work is worse than a blank one —
        # the same reasoning intent_graph_scaffold applies to success criteria.
        "items": [],
    }

    text = HEADER.format(name=name, space=a.space) + "\n" + yaml.safe_dump(
        doc, sort_keys=False, allow_unicode=True, width=100)

    if a.dry_run:
        print(text)
        print(f"  dry run — {len(intents)} intent(s) available to serve")
        return 0

    target.write_text(text)
    print(f"  wrote  {target.relative_to(Path(a.root))}")
    print(f"  intents available: {len(intents)}"
          + ("" if intents else "  — run intent_graph_scaffold.py first, or items cannot serve anything"))
    print(f"  check: python3 .datacore/lib/roadmap_validate.py --space {a.space}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
