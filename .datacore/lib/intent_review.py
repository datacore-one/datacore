#!/usr/bin/env python3
"""Generate an owner-private review of the combined intent graph.

Reports live under DATACORE_STATE/intent-reviews/<installation>/, never in a
source space. --out selects a Markdown filename within that private directory.
Existing shared reports are retained; this command does not publish or migrate
them. Give its OS identity access only to spaces the owner may aggregate.

    python3 intent_review.py --out Intent-Graph-Review.md
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import html
import re
import stat
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from file_utils import atomic_write_text, file_lock, private_state_directory


def _literal(value) -> str:
    """Render source text without executable HTML, links or Markdown structure."""
    value = str(value).replace('\r', ' ').replace('\n', ' ')
    return re.sub(r'([\\`*_{}\[\]()#+.!|>~-])', r'\\\1', html.escape(value, quote=True))


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
    w(f"Generated {_literal(today)} by `.datacore/lib/intent_review.py`. "
      "Owner-private aggregate of `.datacore/intents.org` and discovered "
      "spaces' `org/intents.org`, with priorities from `.datacore/cos/priorities.yaml`.")
    w("")
    w("| | |")
    w("|---|---|")
    w(f"| Nodes | {len(g.nodes)} — {sum(1 for n in g.nodes.values() if n.level == 1)} intents, "
      f"{sum(1 for n in g.nodes.values() if n.level == 2)} goals, "
      f"{sum(1 for n in g.nodes.values() if n.level == 3)} initiatives |")
    w(f"| Frontier | {len(g.frontier())} nodes with nothing open beneath them |")
    w(f"| High-leverage | {sum(1 for n in g.nodes if g.is_high_leverage(n))} "
      "nodes serving more than one intent |")
    w(f"| Open tasks | {r['total']} across all spaces |")
    w(f"| Placed on the graph | {placed} ({100 * placed // max(1, r['total'])}%) — "
      f"{r['by_method']['property']} by property, {r['by_method']['tag']} by tag, "
      f"{r['by_method']['keyword']} by keyword |")
    w(f"| Unplaced | {r['by_method']['none']} — work with no stated why |")
    w("")

    w("## What needs deciding")
    w("")
    w("These are the questions the numbers raise. Everything below is evidence "
      "for them.")
    w("")
    w(f"- {r['by_method']['none']} open tasks have no graph placement. "
      "Do they need an explicit intent link, or is a branch missing?")
    off_graph = sum(1 for s in g.spotlight if s.get('id') not in g.nodes)
    w(f"- {off_graph} of {len(g.spotlight)} stated priorities have no matching node. "
      "Which goal should each serve?")
    w(f"- {by_kind['no_work']} intents have no open work beneath them. "
      "Is that deliberate, or is a next action missing?")
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
            serves = _literal(node.id) if node else "**nothing in the graph**"
            w(f"| {_literal(s['rank'])} | {_literal(s.get('statement') or sid or '')} | {serves} |")
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
        w(f"### {_literal(n.title)}")
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
            crit = _literal(c.success) if c.success else "_(none)_"
            w(f"| {hl} | {_literal(c.title)} | {crit} | {idx.get(cid, 0)} |")
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
                w(f"- **{_literal(node.title if node else x['id'])}** — {_literal(x['id'])}")
    w("")

    if r["unplaced_by_space"]:
        w("## Work with no stated why")
        w("")
        w("| Space | Unplaced tasks |")
        w("|---|---:|")
        for space, n in r["unplaced_by_space"].items():
            w(f"| {_literal(space)} | {n} |")
        w("")
        w("Unplaced is not the same as unimportant. It means either the work "
          "serves nothing stated, or the graph is missing the branch it would "
          "hang from.")
    w("")
    w("---")
    w("")
    w("Regenerate with `python3 .datacore/lib/intent_review.py`.")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--out", default="Intent-Graph-Review.md", help="filename in private runtime state")
    ap.add_argument("--date", default="")
    a = ap.parse_args()
    root = Path(a.root).expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError('data root is not a directory')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\.md', a.out):
        raise ValueError('review output must be a Markdown filename, not a path')
    installation = hashlib.sha256(str(root).encode('utf-8')).hexdigest()
    dest = private_state_directory('intent-reviews/' + installation, data_root=root) / a.out
    today = a.date
    if not today:
        import subprocess
        today = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent / "date_utils.py"), "today"],
            capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        if not today:
            raise RuntimeError("installed date helper returned no date")
    today = date.fromisoformat(today).isoformat()
    # Serialize generation as well as publication: an older slow generation
    # must not finish after a newer invocation and replace its complete report.
    with file_lock(dest):
        try:
            info = dest.lstat()
        except FileNotFoundError:
            pass
        else:
            import os
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or info.st_mode & 0o077):
                raise ValueError('existing review must be a private regular file with one link')
        text = build(root, today)
        atomic_write_text(dest, text)
    print(f"  wrote {dest} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
