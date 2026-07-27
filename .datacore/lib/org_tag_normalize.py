#!/usr/bin/env python3
"""Rewrite invalid org tags (hyphens) to their org spelling (underscores).

Companion to org_tag_audit.py, which finds them. Org tags may only contain
[a-zA-Z0-9_@#%]; one hyphenated tag voids the ENTIRE trailing tag string on
that heading, so its siblings vanish from every query too. The canonical
kebab-case spelling is preserved for markdown per DIP-0014 — tag_utils
.to_org_tag() maps between them.

Only the TRAILING tag string of a heading is touched. Hyphens in heading prose
("re-run the pipeline") and anywhere in bodies, drawers or properties are left
exactly as they are.

    python3 .datacore/lib/org_tag_normalize.py --dry-run
    python3 .datacore/lib/org_tag_normalize.py --apply
    python3 .datacore/lib/org_tag_normalize.py --apply --root ~/Data/0-personal
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

HEADING = re.compile(r"^(\*+\s+)(.*)$")
TRAILING_TAGS = re.compile(r"(:[^\s:]+(?::[^\s:]+)*:)(\s*)$")
VALID_TAG_CHARS = re.compile(r"^[A-Za-z0-9_@#%]+$")
# An orphaned tag run: a second tag-shaped group left in the heading TEXT with
# the real tag string after it. This is what a search-and-replace migration
# leaves behind when it APPENDS corrected tags instead of replacing them —
# the orphaned run's tags are lost, because only the trailing group parses.
ORPHAN_TAGS = re.compile(r"\s(:[^\s:]+(?::[^\s:]+)*:?)\s*$")
LINK = re.compile(r"\[\[[^\]]*\](?:\[[^\]]*\])?\]")


def fix_line(line: str) -> tuple[str, list[str]]:
    """Return (new_line, [renamed tags]) for one heading line."""
    m = HEADING.match(line)
    if not m:
        return line, []
    prefix, text = m.group(1), m.group(2)
    tm = TRAILING_TAGS.search(text)
    if not tm:
        return line, []

    tags = [t for t in tm.group(1).strip(":").split(":") if t]
    if not tags:
        return line, []

    renamed = []

    # Recover an orphaned tag run left in the heading text (see ORPHAN_TAGS).
    # Links are stripped first: their URLs are full of colons and read as
    # tag-shaped runs otherwise.
    body = text[: tm.start()]
    om = ORPHAN_TAGS.search(LINK.sub(lambda m: " " * len(m.group(0)), body))
    if om:
        orphaned = [t for t in om.group(1).strip(":").split(":") if t]
        if len(orphaned) >= 2:
            merged, seen = [], set()
            for t in [o.replace("-", "_") for o in orphaned] + tags:
                if t not in seen:
                    seen.add(t)
                    merged.append(t)
            renamed.append(f"recovered orphaned tags {om.group(1)}")
            tags = merged
            body = body[: om.start()].rstrip() + " "
            text = body + tm.group(1) + tm.group(2)
            tm = TRAILING_TAGS.search(text)
    new_tags = []
    for t in tags:
        if VALID_TAG_CHARS.match(t):
            new_tags.append(t)
            continue
        fixed = t.replace("-", "_")
        if not VALID_TAG_CHARS.match(fixed):
            # Still illegal (e.g. contains "/" or "."): leave it alone and let
            # the audit keep reporting it rather than invent a spelling.
            new_tags.append(t)
            continue
        renamed.append(f"{t} -> {fixed}")
        new_tags.append(fixed)

    if not renamed:
        return line, []

    new_text = text[: tm.start()] + ":" + ":".join(new_tags) + ":" + tm.group(2)
    return prefix + new_text, renamed


def process(path: Path, apply: bool) -> list[tuple[int, str]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeDecodeError):
        return []

    changes: list[tuple[int, str]] = []
    out: list[str] = []
    for n, line in enumerate(lines, 1):
        stripped = line.rstrip("\n")
        new, renamed = fix_line(stripped)
        if renamed:
            changes.append((n, "; ".join(renamed)))
            out.append(new + ("\n" if line.endswith("\n") else ""))
        else:
            out.append(line)

    if changes and apply:
        path.write_text("".join(out), encoding="utf-8")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(Path.home() / "Data"))
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.apply == args.dry_run:
        print("pick exactly one of --apply / --dry-run", file=sys.stderr)
        return 2

    root = Path(args.root).expanduser()
    files = sorted(p for p in root.glob("**/*.org") if ".git" not in p.parts)

    total_headings = 0
    total_files = 0
    for f in files:
        changes = process(f, apply=args.apply)
        if not changes:
            continue
        total_files += 1
        total_headings += len(changes)
        print(f"{f.relative_to(root)}: {len(changes)} heading(s)")
        for n, what in changes[:3]:
            print(f"    line {n}: {what}")
        if len(changes) > 3:
            print(f"    … {len(changes) - 3} more")

    verb = "updated" if args.apply else "would update"
    print(f"\n{verb} {total_headings} heading(s) across {total_files} file(s) "
          f"({len(files)} org files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
