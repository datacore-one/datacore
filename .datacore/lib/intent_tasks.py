#!/usr/bin/env python3
"""Place real tasks on the intent graph, and report what does not fit.

Why
---
The intent graph answers "why does this matter". Until tasks are placed on it,
every gap check runs against the document rather than against reality — you can
prove the graph is internally tidy while nothing beneath it is being done.

Placement is by declaration first, inference second:

  1. an explicit `:INTENT:` property on the task
  2. a focus-area tag mapped in .datacore/intent-map.yaml
  3. keyword overlap with a node title

Order matters. Tasks already carry `:plur:`, `:datacore:`, `:sales:`, so ~20
tag mappings place hundreds of tasks without editing any of them. Keyword
matching is the last resort because it guesses, and a guess recorded as
placement is worse than an honest unplaced count.

Tasks that fit nowhere are NOT an error to suppress. They are the finding:
either the work serves nothing stated, or the graph is missing a branch.

    python3 intent_tasks.py            # coverage report
    python3 intent_tasks.py --json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OPEN_STATES = ("TODO", "NEXT", "WAITING")


def _tasks(root: Path):
    """Every open task across every space, with its space and tags."""
    try:
        from org_workspace import OrgWorkspace
    except ImportError:
        return []
    out = []
    for f in sorted(root.glob("[0-9]-*/org/next_actions.org")):
        ws = OrgWorkspace()
        try:
            ws.load(str(f))
        except Exception:
            continue
        for n in ws.all_nodes():
            if getattr(n, "todo", None) in OPEN_STATES:
                out.append({
                    "space": f.parent.parent.name,
                    "heading": (n.heading or "").strip(),
                    "tags": tuple(n.tags or ()),
                    "intent": n.get_property("INTENT"),
                })
    return out


def place(root: Path, graph) -> dict:
    """Assign each open task to a graph node, or to nothing."""
    tasks = _tasks(root)
    index: dict[str, int] = {}
    unplaced_by_space: dict[str, int] = {}
    by_method = {"property": 0, "tag": 0, "keyword": 0, "none": 0}

    for t in tasks:
        nid, method = None, "none"
        if t["intent"] and t["intent"] in graph.nodes:
            nid, method = t["intent"], "property"
        else:
            for tag in t["tags"]:
                cand = graph.tag_map.get(str(tag).lower())
                if cand and cand in graph.nodes:
                    nid, method = cand, "tag"
                    break
            if nid is None:
                node = graph.match(t["heading"], t["space"], ())
                if node is not None:
                    nid, method = node.id, "keyword"
        by_method[method] += 1
        if nid:
            index[nid] = index.get(nid, 0) + 1
            # Coverage flows upward: work on a leaf is work on its intent.
            for anc in graph.ancestors(nid):
                index[anc] = index.get(anc, 0) + 1
        else:
            unplaced_by_space[t["space"]] = unplaced_by_space.get(t["space"], 0) + 1

    return {"total": len(tasks), "index": index, "by_method": by_method,
            "unplaced_by_space": dict(sorted(unplaced_by_space.items(),
                                             key=lambda kv: -kv[1]))}


def task_index(root: Path, graph) -> dict[str, int]:
    return place(root, graph)["index"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).expanduser()
    from priority_score import IntentGraph
    g = IntentGraph.load(root)
    r = place(root, g)

    if a.json:
        print(json.dumps(r, indent=2))
        return 0

    placed = r["total"] - r["by_method"]["none"]
    pct = (100 * placed // r["total"]) if r["total"] else 0
    print(f"  {r['total']} open tasks, {placed} placed on the graph ({pct}%)")
    print(f"  by method: " + ", ".join(f"{k}={v}" for k, v in r["by_method"].items()))
    if r["unplaced_by_space"]:
        print("\n  unplaced, by space — work with no stated why:")
        for space, n in r["unplaced_by_space"].items():
            print(f"    {space:16} {n:4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
