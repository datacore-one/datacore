#!/usr/bin/env python3
"""Remove inbox.org entries that are already routed to a destination org file.

Why this exists
---------------
`/process-inbox` routes entries out of inbox.org into next_actions.org and
research_learning.org, then leaves inbox.org empty. A later capture that writes
a *stale snapshot* of inbox.org silently resurrects everything the routing had
removed — the entries then live in both places, and because a subsequent
`ensure-ids` assigns the resurrected copies fresh IDs, org-workspace's
`dedup_ids()` cannot see them as duplicates. Headings are the only reliable
join key.

This happened on 2026-07-26: /process-inbox (250072f7) routed 187 entries and
returned inbox.org to empty; capture 1b2d7233 re-added 1512 lines 90 minutes
later, restoring 176 already-routed entries.

Matching
--------
An inbox entry is a duplicate when its *normalised heading* also appears as a
heading in one of the destination files. Normalisation strips the leading stars,
TODO state, priority cookie and trailing tag string, then collapses whitespace.
Deliberately conservative: only exact heading matches are removed, so a
reworded entry survives and gets processed normally.

Usage
-----
    python3 .datacore/lib/inbox_dedup.py --space 0-personal            # dry run
    python3 .datacore/lib/inbox_dedup.py --space 0-personal --apply
    python3 .datacore/lib/inbox_dedup.py --space 0-personal --apply --tag sprint_s1

`--tag` restricts removal to entries carrying that org tag, for when you want to
clean one batch rather than the whole inbox. Default is a dry run: nothing is
written unless `--apply` is passed. With `--apply` the original is copied to
`<inbox>.bak` before rewriting.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

HEADING_RE = re.compile(r"^(\*+)\s+(TODO|NEXT|WAITING|DONE|SOMEDAY|CANCELLED)?\s*(.*?)$")
PRIORITY_RE = re.compile(r"^\[#[A-C]\]\s*")
TAGS_RE = re.compile(r"\s+(:[\w:@#%-]+:)\s*$")

DEFAULT_DESTINATIONS = ("org/next_actions.org", "org/research_learning.org")


def split_heading(line: str) -> tuple[int, str, str] | None:
    """Return (level, normalised_title, tag_string) for an org heading line."""
    m = HEADING_RE.match(line.rstrip())
    if not m:
        return None
    level = len(m.group(1))
    rest = m.group(3)
    tags = ""
    tm = TAGS_RE.search(rest)
    if tm:
        tags = tm.group(1)
        rest = rest[: tm.start()]
    title = PRIORITY_RE.sub("", rest).strip()
    title = re.sub(r"\s+", " ", title)
    return level, title, tags


def destination_titles(paths: list[Path]) -> set[str]:
    titles: set[str] = set()
    for p in paths:
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            parsed = split_heading(line)
            if parsed and parsed[1]:
                titles.add(parsed[1])
    return titles


def dedup(inbox: Path, dests: list[Path], tag: str | None) -> tuple[list[str], list[tuple[str, str]]]:
    """Return (kept_lines, removed_entries) where removed_entries is [(title, tags)]."""
    known = destination_titles(dests)
    lines = inbox.read_text(encoding="utf-8").splitlines()

    kept: list[str] = []
    removed: list[tuple[str, str]] = []

    # Entries are top-level-plus headings under the "* Inbox" root. We drop a
    # heading and every line beneath it until the next heading at the same or a
    # shallower level, so PROPERTIES drawers and bodies go with their heading.
    i = 0
    n = len(lines)
    while i < n:
        parsed = split_heading(lines[i])
        if parsed is None:
            kept.append(lines[i])
            i += 1
            continue

        level, title, tags = parsed
        is_entry = level >= 2 and bool(title)
        tag_ok = tag is None or (tag in tags)

        if is_entry and tag_ok and title in known:
            start = i
            i += 1
            while i < n:
                nxt = split_heading(lines[i])
                if nxt and nxt[0] <= level:
                    break
                i += 1
            removed.append((title, tags))
            # Trailing blank lines belonging to the removed block go too.
            while kept and kept[-1].strip() == "" and i < n:
                break
            del start  # block consumed
            continue

        kept.append(lines[i])
        i += 1

    return kept, removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--space", default="0-personal", help="space directory (default: 0-personal)")
    ap.add_argument("--root", default=str(Path.home() / "Data"), help="datacore root")
    ap.add_argument("--inbox", default="org/inbox.org", help="inbox path relative to space")
    ap.add_argument("--dest", action="append", default=None,
                    help="destination org file relative to space (repeatable). "
                         f"Default: {', '.join(DEFAULT_DESTINATIONS)}")
    ap.add_argument("--tag", default=None, help="only remove entries carrying this org tag")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = ap.parse_args()

    space = Path(args.root) / args.space
    inbox = space / args.inbox
    if not inbox.exists():
        print(f"error: no inbox at {inbox}", file=sys.stderr)
        return 1

    dests = [space / d for d in (args.dest or DEFAULT_DESTINATIONS)]
    missing = [str(d) for d in dests if not d.exists()]
    if missing:
        print(f"warning: destination not found, skipping: {', '.join(missing)}", file=sys.stderr)

    kept, removed = dedup(inbox, dests, args.tag)

    scope = f" tagged :{args.tag}:" if args.tag else ""
    print(f"inbox: {inbox}")
    print(f"destinations: {', '.join(str(d.relative_to(space)) for d in dests if d.exists())}")
    print(f"already-routed entries{scope}: {len(removed)}")
    for title, tags in removed:
        print(f"  - {title[:88]}  {tags}")

    if not removed:
        print("\nnothing to remove.")
        return 0

    if not args.apply:
        print(f"\nDRY RUN — {len(removed)} entries would be removed "
              f"({len(inbox.read_text(encoding='utf-8').splitlines())} → {len(kept)} lines). "
              "Re-run with --apply to write.")
        return 0

    backup = inbox.with_suffix(inbox.suffix + ".bak")
    shutil.copy2(inbox, backup)
    inbox.write_text("\n".join(kept) + "\n", encoding="utf-8")
    print(f"\nremoved {len(removed)} entries. backup: {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
