#!/usr/bin/env python3
"""
Prune superseded slide-render directories.

nano-banana renders accumulate one directory per iteration (slides, slides-v2,
slides-v4-final, slides-v6-nologos...). Each holds 4k PNGs plus a lossless PDF,
so a single deck can reach 2GB. This keeps the NEWEST render dir per deck
(by mtime, never by name — names are unreliable) and reports the rest.

DRY RUN BY DEFAULT. Nothing is deleted without --apply.

Usage:
    python3 prune_slide_renders.py <root> [<root> ...]           # dry run
    python3 prune_slide_renders.py <root> --apply                # delete
    python3 prune_slide_renders.py <root> --keep 2               # keep newest 2

Example:
    python3 .datacore/lib/prune_slide_renders.py \
        1-datafund/1-tracks/comms/presentations \
        1-datafund/1-tracks/comms/proposals \
        5-plur/1-tracks/comms/presentations
"""

import argparse
import shutil
import sys
from pathlib import Path


def dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def render_dirs(deck: Path) -> list[Path]:
    """Render output dirs inside a deck folder — 'slides' or 'slides-*'."""
    return sorted(
        (d for d in deck.iterdir()
         if d.is_dir() and (d.name == "slides" or d.name.startswith("slides-"))),
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="+", help="Directories containing deck folders")
    ap.add_argument("--keep", "-k", type=int, default=1,
                    help="How many newest render dirs to keep per deck (default 1)")
    ap.add_argument("--apply", action="store_true",
                    help="Actually delete. Without this, dry run only.")
    ap.add_argument("--exclude", "-x", action="append", default=[],
                    help="Never drop a render dir whose name contains this "
                         "substring. Repeatable. Use for deliberately-kept "
                         "references (e.g. -x reference -x final).")
    args = ap.parse_args()

    total = 0
    doomed: list[tuple[Path, int]] = []

    for root in args.roots:
        root_path = Path(root)
        if not root_path.is_dir():
            print(f"skip (not a dir): {root}")
            continue

        for deck in sorted(p for p in root_path.iterdir() if p.is_dir()):
            dirs = render_dirs(deck)
            if len(dirs) <= args.keep:
                continue

            keep, drop = dirs[:args.keep], dirs[args.keep:]

            # Pull deliberately-kept dirs back out of the drop list.
            spared = [d for d in drop if any(x.lower() in d.name.lower()
                                             for x in args.exclude)]
            drop = [d for d in drop if d not in spared]
            keep = keep + spared

            if not drop:
                continue

            print(f"\n{deck}")
            for d in keep:
                print(f"   KEEP  {d.name}")
            for d in drop:
                size = dir_size(d)
                total += size
                doomed.append((d, size))
                print(f"   DROP  {d.name}  ({size / 1024 / 1024:.0f} MB)")

    print(f"\n{'=' * 60}")
    print(f"{len(doomed)} directories · {total / 1024 / 1024 / 1024:.2f} GB reclaimable")

    if not args.apply:
        print("DRY RUN — nothing deleted. Re-run with --apply to execute.")
        return 0

    for d, _ in doomed:
        shutil.rmtree(d)
    print(f"Deleted {len(doomed)} directories.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
