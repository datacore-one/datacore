#!/usr/bin/env python3
"""Merge the top-down intent graph with the bottom-up work record.

The method
----------
Two derivations, deliberately independent:

  TOP-DOWN   what the graphs SAY the work is for. Authored from the vision
             posts and strategy documents.
  BOTTOM-UP  what the work record SHOWS was done. Journals, recorded
             decisions, completed tasks — evidence, not intention.

Where they meet is a confirmation, and it means something precisely because
neither derivation flattered the other. Where they diverge is the review
agenda:

  confirmed   both views agree — the graph is earning its place
  orphan      work with no intent above it — either the graph is missing a
              branch, or the work should stop
  dormant     intent with no work beneath it — either not started, or drift
  reversed    intent still stated, but the record says it was killed

That last cell is the one neither view catches alone. The token strategy sat
in the Datacore graph with 19 tasks under it while the fundraising plan
recorded "Token killed" on 2026-07-24.

Configuration is NOT evidence. venture.yaml cadences say what someone declared
should recur; they do not say anything happened. Bottom-up counts only
journals, decisions and completed work.

    python3 intent_merge.py                 # the four cells
    python3 intent_merge.py --since 2026-06-01
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

#: Phrases that mark a reversal. Deliberately narrow, and narrowed FURTHER
#: after a first run fired on "retire" inside a bug report and "dropped" in a
#: changelog: a false reversal retires a live branch, which is worse than
#: missing a real one. Bare "dropped"/"retired" are gone; what remains reads
#: as a decision about a direction, not an event in a log.
REVERSAL = re.compile(
    r"\b(killed|no longer pursuing|decision to stop|walked away from|"
    r"abandoning|we are not (?:doing|pursuing|building))\b", re.I)

#: A journal covers many topics, so a whole file must never resolve to one
#: node. Records are split into sections and each is matched separately.
SECTION = re.compile(r"^#{1,4} ", re.M)

#: Minimum distinct keyword hits before a section counts as evidence. One hit
#: on a common word is coincidence; the first run placed 1256 of 1256 records
#: because a single generic match was enough.
MIN_HITS = 2

#: A line that announces itself as a decision. Reversals are only read from
#: these — see the note where they are detected.
DECISION = re.compile(r"\b(decision|decided|verdict|we will not|going with)\b", re.I)


def completed(root: Path) -> list[dict]:
    """DONE tasks, including archives — the bottom-up evidence.

    Better than journal prose in every way that matters: a DONE task is an
    atomic unit of work that actually finished, it carries its own tags (so it
    places by declaration through the DIP-0014 registry rather than by
    guessing), and it has no surrounding narrative to match against by
    accident. Journal mining placed 79% of sections and most of that was
    coincidence.

    Archives are included deliberately — completed work is moved out of
    next_actions.org, so reading only the live file would show the backlog and
    miss the accomplishment.
    """
    out = []
    heading = re.compile(r"^\*+\s+DONE\s+(.*?)(?:\s+(:[A-Za-z0-9_@#%:]+:))?\s*$")
    for f in sorted(root.glob("[0-9]-*/org/*.org")):
        try:
            txt = f.read_text(errors="ignore")
        except OSError:
            continue
        space = f.parent.parent.name
        for line in txt.splitlines():
            m = heading.match(line)
            if not m:
                continue
            title = re.sub(r"^\[#[ABC]\]\s*", "", m.group(1) or "").strip()
            tags = tuple(t for t in (m.group(2) or "").strip(":").split(":") if t)
            if title:
                out.append({"space": space, "title": title, "tags": tags,
                            "file": f.name})
    return out


def decision_records(root: Path) -> list[dict]:
    """Structured decision records — the only reliable reversal source.

    Three attempts at mining reversals from journal prose each returned noise
    ("run window (was killed at 1h)" is an ops note). These files announce
    themselves as decisions, there are a handful of them, and the one true
    positive found by hand lives here:
    5-plur/3-knowledge/decisions/2026-07-24-token-killed-*.md
    """
    out = []
    for d in sorted(root.glob("[0-9]-*/3-knowledge/decisions")):
        for f in sorted(d.glob("*.md")):
            try:
                text = f.read_text(errors="ignore")
            except OSError:
                continue
            out.append({"space": d.parent.parent.name, "file": f.name,
                        "text": text,
                        "reverses": bool(REVERSAL.search(text[:2000]))})
    return out


def merge(root: Path, since: date) -> dict:
    from priority_score import IntentGraph
    from intent_tasks import place

    g = IntentGraph.load(root)
    open_tasks = place(root, g)["index"]
    done = completed(root)
    decisions = decision_records(root)

    hits: Counter = Counter()
    unplaced: list[dict] = []
    for t in done:
        node = g.match(t["title"], t["space"], t["tags"])
        if node is None:
            unplaced.append(t)
            continue
        hits[node.id] += 1
        for anc in g.ancestors(node.id):
            hits[anc] += 1

    candidates = [d for d in decisions if d["reverses"]]

    # Reversals are not attributed to nodes — see decision_records(). The
    # cells here are the three that CAN be computed from evidence.
    confirmed, dormant = [], []
    for nid, n in g.nodes.items():
        evidence = hits.get(nid, 0)
        tasks = open_tasks.get(nid, 0)
        if evidence:
            confirmed.append((nid, n, tasks, evidence))
        elif not tasks:
            dormant.append((nid, n))

    return {"graph": g, "confirmed": confirmed, "dormant": dormant,
            "candidates": candidates, "done": len(done),
            "unplaced": unplaced, "decisions": len(decisions)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--since", default="2026-06-01")
    a = ap.parse_args()
    root = Path(a.root).expanduser()
    since = date(*map(int, a.since.split("-")))

    r = merge(root, since)
    placed = r["done"] - len(r["unplaced"])
    print(f"  {r['done']} DONE tasks (incl. archives); {placed} placed "
          f"({100 * placed // max(1, r['done'])}%), "
          f"{len(r['unplaced'])} with no intent above them")
    print(f"  {r['decisions']} structured decision records\n")

    print(f"REVERSED — decision records that reverse something ({len(r['candidates'])})")
    for d in r["candidates"]:
        print(f"  {d['space']:12} {d['file'][:70]}")
    print(f"\nDORMANT — stated, no evidence and no tasks ({len(r['dormant'])})")
    for nid, n in r["dormant"][:12]:
        print(f"  {nid[:44]:46} {n.title[:44]}")
    print(f"\nCONFIRMED — graph and completed work agree ({len(r['confirmed'])})")
    for nid, n, tasks, ev in sorted(r["confirmed"], key=lambda x: -x[3])[:12]:
        print(f"  {nid[:44]:46} {ev:>3} done, {tasks:>3} open")
    print(f"\nORPHAN — completed work with no intent above it ({len(r['unplaced'])})")
    for t in r["unplaced"][:10]:
        print(f"  {t['space']:12} {t['title'][:66]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
