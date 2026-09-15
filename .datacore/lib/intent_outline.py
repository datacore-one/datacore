#!/usr/bin/env python3
"""Dump an intent graph as a nested bullet list for human review.

The org file is the machine-readable form; this is the form you can read in one
pass and mark up. Kept as a script rather than a one-off paste because the
graph changes and a stale outline is worse than none.

    python3 intent_outline.py --space example --out Intent-Outline.md
    python3 intent_outline.py                      # every space, to stdout
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from intent_review import _literal, publish

#: Rendered inline after a node, in this order. Everything else is dropped —
#: the point is a page you can scan, not a property dump.
SHOWN = ("SUCCESS", "GATE", "TARGET", "METRIC", "OWNER", "BENCHMARK",
         "WINDOW", "BANNED", "WHY", "STATUS", "NOTE")


def outline(graph, space: str, tasks: dict | None = None) -> list[str]:
    space = graph.canonical_space(space)
    roots = [n for n in graph.nodes.values()
             if (n.id.split(':', 1)[0] if ':' in n.id else '') == space and not n.parent]
    out: list[str] = []

    def walk(node, depth: int):
        pad = "  " * depth
        tags = []
        if graph.is_high_leverage(node.id):
            tags.append("**HL**")
        n_tasks = (tasks or {}).get(node.id, 0)
        if n_tasks:
            tags.append(f"`{n_tasks} tasks`")
        suffix = ("  " + " ".join(tags)) if tags else ""
        out.append(f"{pad}- **{_literal(node.title)}**{suffix}")
        for key in SHOWN:
            val = getattr(node, key.lower(), None)
            if key == "SUCCESS":
                val = node.success
            if val:
                out.append(f"{pad}  - _{key.title()}:_ {_literal(val)}")
        for cid in node.children:
            child = graph.nodes.get(cid)
            if child:
                walk(child, depth + 1)

    for r in sorted(roots, key=lambda n: n.title):
        walk(r, 0)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--space", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--tasks", action="store_true",
                    help="annotate with open task counts (slower)")
    a = ap.parse_args()
    root = Path(a.root).expanduser()

    def build():
        from priority_score import IntentGraph
        g = IntentGraph.load(root)
        idx = None
        if a.tasks:
            from intent_tasks import task_index
            idx = task_index(root, g)
        spaces = [g.canonical_space(a.space)] if a.space else sorted(
            {n.id.split(':', 1)[0] if ':' in n.id else '' for n in g.nodes.values()})
        if a.space and spaces[0] not in g.space_paths:
            raise ValueError('unknown intent source space')
        lines = []
        for sp in spaces:
            lines.append(f"\n## {_literal(sp or 'Installation graph')}\n")
            lines += outline(g, sp, idx)
        return '\n'.join(lines).strip() + '\n'
    if a.out:
        dest, text = publish(root, a.out, build)
        print(f"  wrote {dest} ({len(text.splitlines())} lines)")
    else:
        print(build())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
