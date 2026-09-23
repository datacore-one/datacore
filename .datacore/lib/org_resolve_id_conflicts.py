#!/usr/bin/env python3
"""Resolve org merge conflicts that are ONLY competing auto-assigned :ID: values.

Why this recurs
---------------
Two machines read the same org file and both auto-assign IDs to headings that
lack them (datacore-app's daemon does this on read; so does ensure-ids on the
box). Each side invents a different uuid for the SAME heading, so the next sync
conflicts on pure metadata — no human decision is involved.

Resolution rule: keep the upstream ID, because that is the value already
published and possibly referenced elsewhere.

WHICH SIDE IS UPSTREAM depends on what git is doing. During a rebase the
`<<<<<<< HEAD` side is upstream (the commit being rebased onto). During a merge
-- and git_fleet_sync MERGES, never rebases (DIP-0046) -- `HEAD` is the LOCAL
side and upstream is the `>>>>>>>` side. Until 2026-09-23 this tool always
kept HEAD, i.e. it kept the local id on every fleet sync. It now asks git
which operation is in progress; when it cannot tell (no merge and no rebase in
progress, e.g. a stash pop), it refuses unless told with --keep head|other.

The discarded id is not always unreferenced: an id minted by the adapter's
`add` already has an `item.create` in the event ledger. When it does, the tool
dismisses that item as `kind=housekeeping`, with a reason naming the kept id
(decision G10, 2026-09-23), through the same helper org_dedup_within_file
uses (`ledger_dismiss_housekeeping`). An id the ledger never created, a space
without a ledger, and --check are left alone.

SAFETY: if ANY conflict hunk contains something other than :ID: lines, the file
is left untouched and the script exits non-zero. Real content conflicts are a
human's problem — this only sweeps the mechanical ones.

    python3 .datacore/lib/org_resolve_id_conflicts.py <file> [<file> ...]
    python3 .datacore/lib/org_resolve_id_conflicts.py --check <file>   # report only
    python3 .datacore/lib/org_resolve_id_conflicts.py --keep other <file>  # override
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

import org_transaction

ID_LINE = re.compile(r"^\s*:ID:\s+\S+\s*$")
ID_VALUE = re.compile(r"^\s*:ID:\s+(\S+)\s*$")


def _id_values(lines: list[str]) -> list[str]:
    return [m.group(1) for l in lines if (m := ID_VALUE.match(l))]


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


def upstream_side(path: Path) -> str | None:
    """'head' during a rebase, 'other' during a merge, None when unknown."""
    def git_path(name: str) -> Path | None:
        r = subprocess.run(["git", "-C", str(path.parent), "rev-parse",
                            "--git-path", name], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        p = Path(r.stdout.strip())
        return p if p.is_absolute() else path.parent / p

    for name, side in (("rebase-merge", "head"), ("rebase-apply", "head"),
                       ("MERGE_HEAD", "other")):
        p = git_path(name)
        if p is not None and p.exists():
            return side
    return None


def resolve(path: Path, apply: bool, keep: str = "auto") -> tuple[bool, str]:
    """(ok, message). With `apply`, runs under the org lock (decision Q12,
    2026-09-23): the file is watched before it is read and rewritten with
    `write_org_text`, so a concurrent adapter commit is neither lost nor
    overwritten. A check (`apply=False`) takes no lock."""
    if apply:
        return org_transaction.serialized(_resolve)(path, True, keep)
    return _resolve(path, False, keep)


def _resolve(path: Path, apply: bool, keep: str) -> tuple[bool, str]:
    if apply:
        org_transaction.watch_file(path)
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

    side = upstream_side(path) if keep == "auto" else keep
    if side not in ("head", "other"):
        return False, ("cannot tell which side is upstream (no merge or rebase "
                       "in progress) — pass --keep head|other")

    out: list[str] = []
    discarded: list[tuple[str, str]] = []  # (discarded id, kept id)
    prev = 0
    for start, sep, end, _ in hunks:
        out.extend(lines[prev:start])
        head, other = lines[start + 1:sep], lines[sep + 1:end]
        kept, dropped = (head, other) if side == "head" else (other, head)
        out.extend(kept)
        kept_ids = _id_values(kept)
        for n, gone in enumerate(_id_values(dropped)):
            if gone in kept_ids:
                continue
            match = kept_ids[n] if n < len(kept_ids) else (kept_ids[0] if kept_ids else "none")
            discarded.append((gone, match))
        prev = end + 1
    out.extend(lines[prev:])
    org_transaction.write_org_text(path, "\n".join(out) + "\n")
    msg = (f"resolved {len(hunks)} ID-only conflict(s), kept the "
           f"{'HEAD' if side == 'head' else 'incoming'} (upstream) IDs")
    if discarded:
        from org_dedup_within_file import ledger_dismiss_housekeeping
        for gone, kept_id in discarded:
            status = ledger_dismiss_housekeeping(path, gone, kept_id,
                                                 "org_resolve_id_conflicts")
            msg += f"; ledger {gone}: {status}"
    return True, msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--check", action="store_true", help="report only, do not write")
    ap.add_argument("--keep", choices=("auto", "head", "other"), default="auto",
                    help="which hunk side is upstream (default: ask git)")
    args = ap.parse_args()

    failed = False
    for f in args.files:
        ok, msg = resolve(Path(f), apply=not args.check, keep=args.keep)
        print(f"{'ok ' if ok else 'SKIP'} {f}: {msg}")
        failed |= not ok
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
