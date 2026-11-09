#!/usr/bin/env python3
"""Fold a space's 4-outbox/ into 4-archive/ (owner decision 2026-10-18, DIP-0017 amendment).

The rename DIP-0017 described (4-archive -> 4-outbox) was never finished; the
owner chose to keep the single folder 4-archive/. This moves every file under
`<space>/4-outbox/archive/` to the same relative path under `<space>/4-archive/`
(tracked, untracked and ignored files alike), and the extra entries named with
--route to the destination given. It refuses to overwrite: any collision stops
the run before a file is moved.

    python3 .datacore/lib/merge_outbox_into_archive.py 1-datafund --dry-run
    python3 .datacore/lib/merge_outbox_into_archive.py 1-datafund \
        --route crypto-mondays-proposal.md=4-archive/outreach/crypto-mondays-2026-05/

Afterwards: `git add -A -- 4-outbox 4-archive` in the space and commit with
explicit paths. Byte-identical copies of the outbox module's routing template
and the stock README are removed (the template stays in the module).
"""
import argparse
import filecmp
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / ".datacore/modules/outbox/templates/outbox-routing.yaml"
STOCK_README_FIRST_LINE = "# 4-outbox"
JUNK = {".DS_Store", ".gitkeep"}


def plan(space: Path, routes: dict[str, str]) -> tuple[list[tuple[Path, Path]], list[Path], list[str]]:
    outbox, archive = space / "4-outbox", space / "4-archive"
    moves, drops, problems = [], [], []
    for entry in sorted(outbox.iterdir()) if outbox.is_dir() else []:
        name = entry.name
        if name == "archive":
            for f in sorted(p for p in entry.rglob("*") if p.is_file() or p.is_symlink()):
                if f.name in JUNK:
                    drops.append(f)
                    continue
                moves.append((f, archive / f.relative_to(entry)))
        elif name in JUNK:
            drops.append(entry)
        elif name == "_routing.yaml" and filecmp.cmp(entry, TEMPLATE, shallow=False):
            drops.append(entry)
        elif name == "README.md" and entry.read_text().startswith(STOCK_README_FIRST_LINE):
            drops.append(entry)
        elif name in routes:
            dest = space / routes[name]
            if routes[name].endswith("/"):
                dest = dest / name
            files = [entry] if entry.is_file() else sorted(p for p in entry.rglob("*") if p.is_file())
            for f in files:
                if f.name in JUNK:
                    drops.append(f)
                    continue
                moves.append((f, dest / f.relative_to(entry) if entry.is_dir() else dest))
        else:
            problems.append(f"no route for 4-outbox/{name}")
    for _, dst in moves:
        if dst.exists() or dst.is_symlink():
            problems.append(f"would overwrite {dst.relative_to(space)}")
    return moves, drops, problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("space")
    ap.add_argument("--route", action="append", default=[], help="NAME=DEST (DEST ending in / keeps NAME)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    space = (ROOT / a.space).resolve()
    routes = dict(r.split("=", 1) for r in a.route)
    moves, drops, problems = plan(space, routes)
    if problems:
        print("refused:\n  " + "\n  ".join(problems))
        return 1
    print(f"{a.space}: {len(moves)} files to move, {len(drops)} placeholder/template files to drop")
    if a.dry_run:
        for s, d in moves[:20]:
            print(f"  {s.relative_to(space)} -> {d.relative_to(space)}")
        return 0
    for s, d in moves:
        d.parent.mkdir(parents=True, exist_ok=True)
        os.rename(s, d)
    for f in drops:
        if f.is_file():
            f.unlink()
    for d in sorted((p for p in (space / "4-outbox").rglob("*") if p.is_dir()), key=lambda p: -len(p.parts)):
        if not any(d.iterdir()):
            d.rmdir()
    if (space / "4-outbox").is_dir() and not any((space / "4-outbox").iterdir()):
        (space / "4-outbox").rmdir()
    left = list((space / "4-outbox").rglob("*")) if (space / "4-outbox").exists() else []
    print("4-outbox removed" if not left else f"left in 4-outbox: {[str(p) for p in left][:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
