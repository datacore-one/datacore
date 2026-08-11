#!/usr/bin/env python3
"""Resolve the two conflict shapes convergence keeps producing — and only those.

Converging nine spaces produced the same two conflicts over and over, neither of
which needs a human:

  ID CHURN. Both sides carry a `:ID:` line for the same heading and nothing
  else. This is ENG-2026-0727-004 exactly: the Mac and the box each mint their
  own id for an org heading on read, so the same logical task ends up with two,
  and every sync conflicts on pure bookkeeping. **Ours wins** — not by
  preference, but because the ledger's genesis import keyed on the local `:ID:`,
  so adopting the remote's would orphan every ledger item pointing at it.

  ONE-SIDED ADDITION. One side is empty and the other added lines — a journal
  entry, a cadence record, an appended task. Additive by construction, so the
  union is the answer and neither side loses anything.

Everything else is left alone and reported. Two sides that both edited the same
lines is genuine disagreement about content, and a tool that guesses there would
eventually guess wrong silently — which is worse than a conflict marker, because
a marker is visible.

Dry-run by default.

    resolve_ledger_conflicts.py <repo> [--apply]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

MARKERS = ("<<<<<<<", "=======", ">>>>>>>")


def conflicted(repo: Path) -> list[str]:
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=U"],
                       cwd=repo, capture_output=True, text=True)
    return [x for x in r.stdout.splitlines() if x.strip()]


def blocks(lines: list[str]):
    """Yield (start, mid, end) indices for each conflict region."""
    i = 0
    while i < len(lines):
        if lines[i].startswith("<<<<<<<"):
            mid = next(j for j in range(i + 1, len(lines)) if lines[j].startswith("======="))
            end = next(j for j in range(mid + 1, len(lines)) if lines[j].startswith(">>>>>>>"))
            yield i, mid, end
            i = end + 1
        else:
            i += 1


def classify(ours: list[str], theirs: list[str]) -> str:
    if all((":ID:" in x or not x.strip()) for x in ours + theirs):
        return "id-churn"
    if not [x for x in ours if x.strip()] or not [x for x in theirs if x.strip()]:
        return "one-sided"
    return "content"


def resolve_file(path: Path) -> tuple[list[str], dict]:
    lines = path.read_text().splitlines()
    out: list[str] = []
    counts = {"id-churn": 0, "one-sided": 0, "content": 0}
    i = 0
    regions = {s: (m, e) for s, m, e in blocks(lines)}
    while i < len(lines):
        if i in regions:
            m, e = regions[i]
            ours, theirs = lines[i + 1:m], lines[m + 1:e]
            kind = classify(ours, theirs)
            counts[kind] += 1
            if kind == "id-churn":
                out += ours
            elif kind == "one-sided":
                out += ours + theirs
            else:
                out += lines[i:e + 1]        # leave the markers in place
            i = e + 1
        else:
            out.append(lines[i]); i += 1
    return out, counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    files = conflicted(args.repo)
    if not files:
        print("no conflicted files")
        return 0

    total = {"id-churn": 0, "one-sided": 0, "content": 0}
    for rel in files:
        p = args.repo / rel
        merged, counts = resolve_file(p)
        for k in total:
            total[k] += counts[k]
        left = counts["content"]
        print(f"  {rel}: id-churn={counts['id-churn']} one-sided={counts['one-sided']} "
              f"content={left}{'  <-- HUMAN' if left else ''}")
        if args.apply and not left:
            p.write_text("\n".join(merged) + "\n")
            subprocess.run(["git", "add", "--", rel], cwd=args.repo,
                           capture_output=True)

    verb = "resolved" if args.apply else "would resolve"
    print(f"\n{verb} {total['id-churn']} id-churn + {total['one-sided']} one-sided; "
          f"{total['content']} content conflict(s) left for a human")
    if not args.apply:
        print("dry run — re-run with --apply")
    return 1 if total["content"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
