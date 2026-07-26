#!/usr/bin/env python3
"""Rename an org-mode tag across every org file in the installation.

WHY THIS EXISTS
---------------
Org-mode tags may only contain [a-zA-Z0-9_@#%]. A hyphen makes the tag invalid,
and — critically — ONE invalid tag voids the ENTIRE tag string on that heading,
so its siblings are dropped too. A heading tagged

    :ops:sync:hygiene:wrap-up-extracted:

parses as having NO tags at all (only inherited FILETAGS survive). grep still
finds the text, which is why this can go unnoticed for a long time: the tag
looks present in the file and is simply absent from every query.

Discovered 2026-07-26: the `wrap-up-extracted` tag mandated by the /wrap-up
spec had silently voided tags on 167 headings across 14 files in 8 spaces.
Same bug class as the `sprint-s1` -> `sprint_s1` fix on 2026-07-25.

Usage:
    python3 .datacore/lib/migrate_org_tag.py --old wrap-up-extracted --new wrap_up_extracted --dry-run
    python3 .datacore/lib/migrate_org_tag.py --old wrap-up-extracted --new wrap_up_extracted --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VALID_TAG = re.compile(r"^[A-Za-z0-9_@#%]+$")

# An org heading's trailing tag block: whitespace, then :a:b:c: at end of line.
HEADING_TAGS = re.compile(r"^(\*+\s+.*?)(\s+)(:(?:[^\s:]+:)+)\s*$")


def find_org_files() -> list[Path]:
    out: list[Path] = []
    for space in sorted(ROOT.glob("[0-9]-*")):
        for sub in ("org",):
            d = space / sub
            if d.is_dir():
                out.extend(sorted(d.glob("*.org")))
    return out


def migrate_line(line: str, old: str, new: str) -> tuple[str, bool]:
    m = HEADING_TAGS.match(line.rstrip("\n"))
    if not m:
        return line, False
    prefix, gap, tagblock = m.group(1), m.group(2), m.group(3)
    tags = [t for t in tagblock.split(":") if t]
    if old not in tags:
        return line, False
    tags = [new if t == old else t for t in tags]
    return f"{prefix}{gap}:{':'.join(tags)}:\n", True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True, help="tag to replace (invalid form)")
    ap.add_argument("--new", required=True, help="replacement tag (must be org-valid)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not VALID_TAG.match(args.new):
        print(f"error: --new '{args.new}' is not a valid org tag "
              f"(allowed: A-Z a-z 0-9 _ @ # %%)", file=sys.stderr)
        return 2
    if VALID_TAG.match(args.old):
        print(f"warning: --old '{args.old}' is already a valid org tag; "
              f"this migration may be unnecessary", file=sys.stderr)

    files = find_org_files()
    total_headings, total_files = 0, 0
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        except (UnicodeDecodeError, OSError) as exc:
            print(f"  ! skip {path}: {exc}", file=sys.stderr)
            continue
        changed = 0
        for i, line in enumerate(lines):
            new_line, hit = migrate_line(line, args.old, args.new)
            if hit:
                lines[i] = new_line
                changed += 1
        if not changed:
            continue
        total_files += 1
        total_headings += changed
        rel = path.relative_to(ROOT)
        print(f"  {rel}: {changed} heading(s)")
        if args.apply:
            path.write_text("".join(lines), encoding="utf-8")

    verb = "would update" if args.dry_run else "updated"
    print(f"\n{verb} {total_headings} heading(s) across {total_files} file(s) "
          f"({len(files)} org files scanned)")
    if args.dry_run and total_headings:
        print("re-run with --apply to write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
