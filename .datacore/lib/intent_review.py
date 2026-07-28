#!/usr/bin/env python3
"""Generate the intent graph review document.

The source doc prescribes a weekly review ("which intents got zero work this
week? any stale nodes?") and a monthly one ("are the 5 intents still the right
5?"). Those questions need current numbers to answer, so this regenerates them
rather than leaving a hand-written snapshot to rot.

    python3 intent_review.py --out 2-datacore/1-tracks/ops/Intent-Graph-Review.md
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def build(root: Path, today: str) -> str:
    from priority_score import IntentGraph
    from intent_tasks import place

    g = IntentGraph.load(root)
    r = place(root, g)
    idx = r["index"]
    gaps = g.gaps(idx)
    by_kind = Counter(x["kind"] for x in gaps)
    placed = r["total"] - r["by_method"]["none"]

    L: list[str] = []
    w = L.append
    w("# Intent Graph — review")
    w("")
    w(f"Generated {today} by `.datacore/lib/intent_review.py`. "
      "Source of structure: `2-datacore/1-tracks/ops/Intent-Graph.md` "
      "(adapted from Swarm Foundation `Mission.md`), machine-readable copy at "
      "`.datacore/intents.org`.")
    w("")
    w("| | |")
    w("|---|---|")
    w(f"| Nodes | {len(g.nodes)} — 5 intents, "
      f"{sum(1 for n in g.nodes.values() if n.level == 2)} goals, "
      f"{sum(1 for n in g.nodes.values() if n.level == 3)} initiatives |")
    w(f"| Frontier | {len(g.frontier())} nodes with nothing open beneath them |")
    w(f"| High-leverage | {sum(1 for n in g.nodes if g.is_high_leverage(n))} "
      "nodes serving more than one intent |")
    w(f"| Open tasks | {r['total']} across all spaces |")
    w(f"| Placed on the graph | {placed} ({100 * placed // max(1, r['total'])}%) — "
      f"{r['by_method']['tag']} by tag, {r['by_method']['keyword']} by keyword |")
    w(f"| Unplaced | {r['by_method']['none']} — work with no stated why |")
    w("")

    w("## What needs deciding")
    w("")
    w("These are the questions the numbers raise. Everything below is evidence "
      "for them.")
    w("")
    w("1. **The graph describes Datacore only.** Its vision is *\"Datacore makes "
      "people smarter by remembering what they do.\"* Six other ventures — "
      "Datafund, FDS, Forge, Meridian, Megaphone, plus personal — have no "
      "branch. Either the graph broadens to cover the whole portfolio, or it "
      "stays Datacore-scoped and the others get their own.")
    w("2. **PLUR Enterprise has no node.** It is the current rank-1 priority "
      "with a signed contract, and the graph's revenue goals are exchange "
      "commission, token, costs and service providers — none of them "
      "enterprise licensing. This is the clearest thing to add.")
    w("3. **Both current priorities link to nothing.** Neither names a graph "
      "node, so this week's stated focus cannot inherit importance from any "
      "stated goal.")
    w("")

    w("## Priorities now")
    w("")
    if not g.spotlight:
        w("_None stated._")
    else:
        w("| Rank | Priority | Serves |")
        w("|---:|---|---|")
        for s in sorted(g.spotlight, key=lambda x: x["rank"]):
            sid = s.get("id")
            node = g.nodes.get(sid) if sid else None
            serves = f"`{node.id}`" if node else "**nothing in the graph**"
            w(f"| {s['rank']} | {s.get('statement') or sid} | {serves} |")
    w("")
    w("Priorities are deliberately not part of the graph. The graph is where "
      "you are heading; the priority list is what you are doing about it this "
      "week. Re-ranking on Monday should not read as a change of direction.")
    w("")

    w("## Goals, by intent")
    w("")
    w("`HL` marks a node serving more than one intent — the source doc's rule "
      "is that these get worked on first. `Tasks` counts open tasks placed on "
      "the node or anything beneath it.")
    w("")
    for n in sorted(g.nodes.values(), key=lambda x: x.title):
        if n.level != 1:
            continue
        w(f"### {n.title}")
        w("")
        w(f"_{idx.get(n.id, 0)} open tasks beneath this intent._")
        w("")
        w("| | Goal | Success criterion | Tasks |")
        w("|---|---|---|---:|")
        for cid in n.children:
            c = g.nodes.get(cid)
            if not c or c.level != 2:
                continue
            hl = "**HL**" if g.is_high_leverage(cid) else ""
            crit = c.success or "_(none)_"
            w(f"| {hl} | {c.title} | {crit} | {idx.get(cid, 0)} |")
        w("")

    w("## Gaps")
    w("")
    if not gaps:
        w("_None._")
    else:
        w("| Kind | Count | Meaning |")
        w("|---|---:|---|")
        meaning = {
            "frontier_no_work": "nothing open beneath it and no tasks — this is "
                                "where the next action has to be defined",
            "spotlight_off_graph": "a stated priority that serves no stated goal",
            "no_work": "an intent with no open tasks anywhere beneath it",
            "ignored_high_leverage": "multi-intent node with no tasks, against "
                                     "the graph's own priority rule",
            "unmeasurable": "goal with no success criterion",
            "uninstrumented": "success criterion that cannot be measured",
            "broken_link": "serves a node that does not exist",
        }
        for k, v in by_kind.most_common():
            w(f"| `{k}` | {v} | {meaning.get(k, '')} |")
        w("")
        w("### Frontier — where work is undefined")
        w("")
        for x in gaps:
            if x["kind"] == "frontier_no_work":
                node = g.nodes.get(x["id"])
                w(f"- **{node.title if node else x['id']}** — `{x['id']}`")
    w("")

    if r["unplaced_by_space"]:
        w("## Work with no stated why")
        w("")
        w("| Space | Unplaced tasks |")
        w("|---|---:|")
        for space, n in r["unplaced_by_space"].items():
            w(f"| {space} | {n} |")
        w("")
        w("Unplaced is not the same as unimportant. It means either the work "
          "serves nothing stated, or the graph is missing the branch it would "
          "hang from — which is decision 1 above.")
    w("")
    w("---")
    w("")
    w("Regenerate with `python3 .datacore/lib/intent_review.py`.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--out", default="2-datacore/1-tracks/ops/Intent-Graph-Review.md")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    root = Path(a.root).expanduser()
    today = a.date
    if not today:
        import subprocess
        today = subprocess.run(
            [sys.executable, str(root / ".datacore" / "lib" / "date_utils.py"), "today"],
            capture_output=True, text=True).stdout.strip() or "unknown"
    text = build(root, today)
    dest = root / a.out
    dest.write_text(text)
    print(f"  wrote {dest} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
