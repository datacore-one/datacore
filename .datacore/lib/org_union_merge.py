#!/usr/bin/env python3
"""Union two diverged versions of an Org file by :ID:, losing no item.

WHY NOT A TEXT MERGE. When two machines both capture into the same Org file and
cannot sync for a while, git sees one enormous conflicted hunk and every
resolution it offers is lossy: `--ours` and `-X theirs` each discard whatever
the other side captured. Measured 2026-09-16 on 0-personal/org/inbox.org --
241 items existed only on the executor host and 140 only on origin. Any
side-pick silently threw away hundreds of real captures.

An Org file is not lines, it is a set of items with identities. Keyed on `:ID:`
the merge is almost entirely mechanical: an item on one side is kept, an item on
both sides is kept once. Only an item that exists on BOTH sides with DIFFERENT
content needs a rule, and in practice there are very few -- one, in that same
measurement.

THE ONE RULE, and its limit. A recorded closure is strictly later information
than the open state it closes: if one side has a terminal state with a `CLOSED:`
stamp and the other does not, the closed one wins. Anything else -- both closed
and differing, neither closed and differing -- is a real disagreement this tool
REFUSES rather than guesses at, naming the ids so a human can look. Refusing is
the point: a merge tool that always produces an answer is how the hundreds of
items got lost in the first place.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HEADING = re.compile(r"^(\*+) ")
ID = re.compile(r"^\s*:ID:\s*(\S+)\s*$", re.M)
#: DIP-0009 v2.0 terminal states. DEFERRED is closed-but-wakeable, not terminal.
TERMINAL = ("DONE", "CANCELLED")
CLOSED = re.compile(r"^\s*CLOSED:\s*\[", re.M)


def split(text: str) -> tuple[str, list[tuple[str | None, str]]]:
    """(preamble, [(id-or-None, block)]) preserving file order and bytes."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, l in enumerate(lines) if HEADING.match(l)]
    if not starts:
        return text, []
    preamble = "".join(lines[: starts[0]])
    blocks: list[tuple[str | None, str]] = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        block = "".join(lines[s:e])
        found = ID.search(block)
        blocks.append((found.group(1) if found else None, block))
    return preamble, blocks


def _closed(block: str) -> bool:
    head = block.splitlines()[0] if block else ""
    state = head.split(None, 2)[1] if len(head.split(None, 2)) > 1 else ""
    return state in TERMINAL and bool(CLOSED.search(block))


def reconcile(a: str, b: str) -> tuple[str, dict]:
    """Union `a` (ours) with `b` (theirs). Raises ValueError on a real clash."""
    pre_a, items_a = split(a)
    _, items_b = split(b)
    by_b = {i: blk for i, blk in items_b if i}

    out: list[str] = []
    seen: set[str] = set()
    stats = {"kept_ours": 0, "kept_theirs": 0, "shared": 0, "closure_wins": 0,
             "unidentified": 0}
    clashes: list[str] = []

    for iid, block in items_a:
        if iid is None:
            out.append(block); stats["unidentified"] += 1; continue
        seen.add(iid)
        other = by_b.get(iid)
        if other is None or other == block:
            out.append(block)
            stats["shared" if other is not None else "kept_ours"] += 1
            continue
        if _closed(other) and not _closed(block):
            out.append(other); stats["closure_wins"] += 1
        elif _closed(block) and not _closed(other):
            out.append(block); stats["closure_wins"] += 1
        else:
            clashes.append(iid)
            out.append(block)

    for iid, block in items_b:
        if iid is not None and iid not in seen:
            out.append(block); stats["kept_theirs"] += 1
        elif iid is None and block not in a:
            out.append(block); stats["unidentified"] += 1

    if clashes:
        raise ValueError("items differ on both sides with no closure to decide "
                         "them: " + ", ".join(sorted(clashes)))
    return pre_a + "".join(out), stats


def _show(ref: str, path: str, repo: Path) -> str:
    r = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cannot read {ref}:{path}: {r.stderr.strip()}")
    return r.stdout


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="org file, relative to the repo")
    ap.add_argument("--repo", type=Path, default=Path("."))
    ap.add_argument("--ours", default="HEAD")
    ap.add_argument("--theirs", default="origin/main")
    ap.add_argument("--apply", action="store_true", help="write the result (default: report)")
    args = ap.parse_args()

    ours = _show(args.ours, args.path, args.repo)
    theirs = _show(args.theirs, args.path, args.repo)
    try:
        merged, stats = reconcile(ours, theirs)
    except ValueError as exc:
        print(f"REFUSED — {exc}", file=sys.stderr)
        return 2

    before = len(split(ours)[1]), len(split(theirs)[1])
    print(f"{args.path}: {before[0]} + {before[1]} -> {len(split(merged)[1])} item(s)")
    for k, v in stats.items():
        if v:
            print(f"  {k.replace('_', ' ')}: {v}")
    if args.apply:
        (args.repo / args.path).write_text(merged, encoding="utf-8")
        print(f"wrote {args.repo / args.path}")
    else:
        print("re-run with --apply to write it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
