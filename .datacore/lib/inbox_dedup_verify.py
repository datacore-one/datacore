#!/usr/bin/env python3
"""Independently verify what inbox_dedup.py would remove.

inbox_dedup.py decides an inbox entry is a duplicate by normalising headings on
BOTH sides and comparing. If that normalisation is wrong in the same way twice,
the script agrees with itself and deletes tasks that were never routed anywhere.
This checks the same removals a different way: plain substring search for the
raw heading text in the destination files, no shared normalisation.

    python3 .datacore/lib/inbox_dedup_verify.py --space 0-personal

Exit 0 = every proposed removal was found in a destination file.
Exit 1 = at least one removal is UNCONFIRMED — inspect before applying.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent


def load_dedup():
    spec = importlib.util.spec_from_file_location("inbox_dedup", LIB / "inbox_dedup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--space", default="0-personal")
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--tag", default=None)
    args = ap.parse_args()

    dedup = load_dedup()
    space = Path(args.root) / args.space
    inbox = space / "org" / "inbox.org"
    dests = [space / d for d in dedup.DEFAULT_DESTINATIONS]

    _, removed = dedup.dedup(inbox, dests, args.tag)
    haystack = "\n".join(p.read_text(encoding="utf-8") for p in dests if p.exists())

    unconfirmed = []
    for title, tags in removed:
        # Substring search on the raw title — deliberately dumber than the
        # script's matcher, so a shared normalisation bug cannot hide here.
        if title not in haystack:
            unconfirmed.append((title, tags))

    print(f"proposed removals: {len(removed)}")
    print(f"confirmed present in destinations: {len(removed) - len(unconfirmed)}")
    if unconfirmed:
        print(f"\nUNCONFIRMED ({len(unconfirmed)}) — would be lost, not moved:")
        for title, tags in unconfirmed:
            print(f"  - {title[:110]}  {tags}")
        return 1
    print("\nAll proposed removals exist in a destination file. Safe to --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
