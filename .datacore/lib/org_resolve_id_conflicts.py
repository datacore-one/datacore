#!/usr/bin/env python3
"""Resolve org merge conflicts that are ONLY competing auto-assigned :ID: values.

Why this recurs
---------------
Two machines read the same org file and both auto-assign IDs to headings that
lack them (datacore-app's daemon does this on read; so does ensure-ids on the
box). Each side invents a different uuid for the SAME heading, so the next sync
conflicts on pure metadata — no human decision is involved.

Resolution rule: keep the upstream (HEAD / "ours" during a rebase) ID, because
that is the value already published and possibly referenced elsewhere. The
local ID is discarded; nothing references it yet.

SAFETY: if ANY conflict hunk contains something other than :ID: lines, the file
is left untouched and the script exits non-zero. Real content conflicts are a
human's problem — this only sweeps the mechanical ones.

    python3 .datacore/lib/org_resolve_id_conflicts.py <file> [<file> ...]
    python3 .datacore/lib/org_resolve_id_conflicts.py --check <file>   # report only
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ID_LINE = re.compile(r"^\s*:ID:\s+\S+\s*$")


def parse_hunks(lines: list[str]) -> list[tuple[int, int, int, int]]:
    """Return (start, sep, end, _) indices for each conflict hunk."""
    hunks = []
    start = sep = None
    for i, line in enumerate(lines):
        if line.startswith("<<<<<<<"):
            start, sep = i, None
        elif line.startswith("=======") and start is not None:
            sep = i
        elif line.startswith(">>>>>>>") and start is not None and sep is not None:
            hunks.append((start, sep, i, 0))
            start = sep = None
    return hunks


def resolve(path: Path, apply: bool) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    if "<<<<<<<" not in text:
        return True, "no conflicts"
    lines = text.splitlines()
    hunks = parse_hunks(lines)
    if not hunks:
        return False, "conflict markers present but unparseable"

    for start, sep, end, _ in hunks:
        body = lines[start + 1:sep] + lines[sep + 1:end]
        if not body or not all(ID_LINE.match(l) for l in body):
            return False, (
                f"hunk at line {start + 1} is not ID-only — resolve by hand"
            )

    if not apply:
        return True, f"{len(hunks)} ID-only conflict(s) — safe to resolve"

    out: list[str] = []
    prev = 0
    for start, sep, end, _ in hunks:
        out.extend(lines[prev:start])
        out.extend(lines[start + 1:sep])  # keep the HEAD/upstream side
        prev = end + 1
    out.extend(lines[prev:])
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True, f"resolved {len(hunks)} ID-only conflict(s), kept upstream IDs"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--check", action="store_true", help="report only, do not write")
    args = ap.parse_args()

    failed = False
    for f in args.files:
        ok, msg = resolve(Path(f), apply=not args.check)
        print(f"{'ok ' if ok else 'SKIP'} {f}: {msg}")
        failed |= not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
