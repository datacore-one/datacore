#!/usr/bin/env python3
"""Re-point org `:ID:`s at the ledger items that were imported from them.

On 2026-08-11 at 13:45 UTC a `cos: local autosave` on winston rewrote **1,204
`:ID:` lines** in one commit — every id in `0-personal/org/next_actions.org` —
and pushed it. Eight of nine spaces lost ledger↔org correspondence entirely:
0-personal went from 602/602 matching ids to **0**. The ledger was untouched;
the org side moved out from under it.

`org_workspace.generate_id()` is `org-YYYYMMDD-HHMMSS-{sha8}` — **timestamped**,
so a regenerated id can never reproduce the original. Recovery by re-running the
generator is therefore impossible; the mapping has to come from something stable.

Headings are that something. Genesis imported each org task as an item whose
`title` is the heading verbatim, so a heading match re-establishes the pairing
the id used to carry. Where a heading is ambiguous — the same text under two
tasks — this refuses rather than guessing, because a wrong re-point silently
attaches a task's history to a different task, which is worse than a missing id.

Deliberately NOT done: rewriting the ledger. It is append-only, so the ids it
holds are the fixed points and org is what moves back.

Dry-run by default.

    restore_ledger_ids.py [--root DIR] [--space NAME] [--apply]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ID_RE = re.compile(r"^(\s*):ID:\s*(\S+)\s*$")


def ledger_titles(space: Path) -> dict[str, str]:
    """title -> id, for unambiguous titles only."""
    g = space / ".datacore" / "events" / "genesis.jsonl"
    if not g.exists():
        return {}
    titles: list[tuple[str, str]] = []
    for line in g.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "item.create":
            continue
        p = e.get("payload") or {}
        if p.get("id") and p.get("title"):
            titles.append((p["title"], p["id"]))
    counts = Counter(t for t, _ in titles)
    return {t: i for t, i in titles if counts[t] == 1}


def org_headings(path: Path) -> list[tuple[int, str, int, str]]:
    """(heading_line_no, heading_text, id_line_no, current_id) per task with an ID."""
    lines = path.read_text(errors="replace").splitlines()
    out = []
    cur_h, cur_hl = None, None
    for n, ln in enumerate(lines):
        if ln.startswith("*"):
            m = re.match(r"^\*+\s+(?:(?:TODO|NEXT|WAITING|DONE|QUEUED|WORKING|REVIEW|FAILED|DEFERRED)\s+)?"
                         r"(?:\[#[ABC]\]\s+)?(.*?)(?:\s+:[\w:@]+:)?\s*$", ln)
            cur_h = (m.group(1).strip() if m else ln.lstrip("* ").strip())
            cur_hl = n
        else:
            m = ID_RE.match(ln)
            if m and cur_h is not None:
                out.append((cur_hl, cur_h, n, m.group(2)))
    return out


def restore_space(space: Path, apply: bool) -> dict:
    org = space / "org" / "next_actions.org"
    if not org.exists():
        return {}
    want = ledger_titles(space)
    if not want:
        return {}
    rows = org_headings(org)
    lines = org.read_text(errors="replace").splitlines()

    # A heading that appears TWICE in org would have both copies re-pointed at
    # the same ledger id, minting duplicate ids — the very churn this repairs.
    # 0-personal alone had 20 such headings (564 matches against 544 unambiguous
    # ledger titles). Ambiguity on either side disqualifies the pair.
    org_counts = Counter(h for _, h, _, _ in rows)
    ambiguous = {h for h, c in org_counts.items() if c > 1}

    matched = restored = already = skipped = 0
    for _, heading, id_line, cur in rows:
        target = want.get(heading)
        if not target:
            continue
        if heading in ambiguous:
            skipped += 1
            continue
        matched += 1
        if target == cur:
            already += 1
            continue
        indent = ID_RE.match(lines[id_line]).group(1)
        lines[id_line] = f"{indent}:ID: {target}"
        restored += 1

    if apply and restored:
        org.write_text("\n".join(lines) + "\n")
    return {"space": space.name, "ledger": len(want), "matched": matched,
            "already": already, "restored": restored, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--space")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    total = 0
    for sp in sorted(args.root.glob("[0-9]-*")):
        if args.space and sp.name != args.space:
            continue
        r = restore_space(sp, args.apply)
        if not r:
            continue
        total += r["restored"]
        print(f"  {r['space']:<12} ledger={r['ledger']:4d} matched={r['matched']:4d} "
              f"already={r['already']:4d} restored={r['restored']:4d} "
              f"ambiguous-skipped={r['skipped']:3d}")

    verb = "restored" if args.apply else "would restore"
    print(f"\n{verb} {total} id(s)")
    if not args.apply:
        print("dry run — re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
