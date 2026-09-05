#!/usr/bin/env python3
"""Keep a space's org/inbox.org a collection point, nothing else.

The inbox collects captures that get sorted into their buckets. In
practice agents close items in place and append new ones at the end of
the file, so over months a file grows two layers that contradict each
other: a "* Inbox" section with the open entries, then hundreds of DONE
and CANCELLED tasks as top-level headings, with fresh captures nested
under them because "**" headings appended at the end become children of
whatever heading was last. On 2026-09-05, 0-personal held 210 closed
tasks at top level and 105 open entries buried under them, 85 of them
tabs captured that afternoon.

This tool restores the invariant:

  1. a "* Inbox" section exists (created after the preamble when missing);
  2. every open entry (TODO/NEXT/WAITING/REVIEW) that sits under a closed
     top-level task moves into the Inbox section;
  3. every closed entry (DONE/CANCELLED/DEFERRED) leaves the file for
     org/inbox-archive-<today>.org under "* Archived (processed <today>)",
     in the convention of the earlier archive files: top-level entries are
     demoted one level so the archive heading is their parent, deeper
     entries move as they are.

org-workspace's archive_done cannot do this: it treats level 1 as
structural and only archives level 3 and deeper, which in a flat inbox is
nothing -- that is why nightly hygiene reported archived=0 for months.

Usage: inbox_cleanup.py <space-dir> [--apply] [--today YYYY-MM-DD]
Without --apply it reports what it would do. Exit 0 either way; exit 2
when the result would not parse.
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path

OPEN = ("TODO", "NEXT", "WAITING", "REVIEW")
CLOSED = ("DONE", "CANCELLED", "DEFERRED")
HEAD = re.compile(r"^(\*+) (?:\[NEEDS_REVIEW\] )?(TODO|NEXT|WAITING|REVIEW|DONE|CANCELLED|DEFERRED)?\b\s*(.*)$")
INBOX = re.compile(r"^\* +inbox\s*$", re.IGNORECASE)


def state_of(line: str):
    m = HEAD.match(line)
    return m.group(2) if m else None


def split_top(lines):
    """-> (preamble, [top-level blocks])"""
    blocks, cur = [], []
    for l in lines:
        if l.startswith("* "):
            blocks.append(cur); cur = [l]
        else:
            cur.append(l)
    blocks.append(cur)
    return blocks[0], blocks[1:]


def split_l2(block):
    """top-level block -> (own lines, [level-2 subtrees])"""
    own, subs, s = [], [], None
    for l in block:
        if l.startswith("** "):
            s = [l]; subs.append(s)
        elif s is None:
            own.append(l)
        else:
            s.append(l)
    return own, subs


def demote(block):
    return [("*" + l) if re.match(r"^\*+ ", l) else l for l in block]


def clean(text: str, today: str):
    lines = text.split("\n")
    pre, tops = split_top(lines)
    # 1. the Inbox section
    idx = next((i for i, b in enumerate(tops) if INBOX.match(b[0])), None)
    created = False
    if idx is None:
        tops.insert(0, ["* Inbox", ""]); idx = 0; created = True
    moved, archived = [], []
    new_tops = []
    for i, b in enumerate(tops):
        if i == idx:
            new_tops.append(b); continue
        st = state_of(b[0])
        own, subs = split_l2(b)
        if st in CLOSED:
            # open children go to the Inbox; the rest leaves with the parent
            orphans = [s for s in subs if state_of(s[0]) in OPEN]
            keep = [s for s in subs if state_of(s[0]) not in OPEN]
            moved.extend(orphans)
            archived.append(demote(own + [l for s in keep for l in s]))
            continue
        # an open task or a section at top level stays; its closed children leave
        closed_kids = [s for s in subs if state_of(s[0]) in CLOSED]
        rest = [s for s in subs if state_of(s[0]) not in CLOSED]
        archived.extend(closed_kids)
        new_tops.append(own + [l for s in rest for l in s])
    # the Inbox section itself: closed children leave, orphans arrive at its end
    own, subs = split_l2(new_tops[idx])
    closed_kids = [s for s in subs if state_of(s[0]) in CLOSED]
    rest = [s for s in subs if state_of(s[0]) not in CLOSED]
    archived.extend(closed_kids)
    inbox = own + [l for s in rest for l in s]
    while len(inbox) > 1 and inbox[-1].strip() == "":
        inbox.pop()
    inbox += [l for s in moved for l in s]
    new_tops[idx] = inbox + [""]
    out = "\n".join(pre + [l for b in new_tops for l in b]).rstrip("\n") + "\n"
    arch = None
    if archived:
        arch = (f"#+TITLE: Inbox Archive {today}\n\n* Archived (processed {today})\n"
                + "\n".join(l for b in archived for l in b).rstrip("\n") + "\n")
    return out, arch, {"inbox_created": created, "moved_into_inbox": len(moved), "archived": len(archived)}


def parses(path: Path) -> bool:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from org_workspace import OrgWorkspace
        ws = OrgWorkspace(); ws.load(str(path)); return True
    except Exception as e:  # noqa: BLE001
        print(f"  does not parse: {e}", file=sys.stderr); return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("space"); ap.add_argument("--apply", action="store_true")
    ap.add_argument("--today", default=date.today().isoformat())
    a = ap.parse_args()
    src = Path(a.space) / "org" / "inbox.org"
    if not src.exists():
        print(f"{a.space}: no org/inbox.org"); return 0
    out, arch, stats = clean(src.read_text(), a.today)
    target = src.with_name(f"inbox-archive-{a.today}.org")
    if arch and target.exists():
        # a second run the same day appends to the day's archive
        arch = target.read_text().rstrip("\n") + "\n" + arch.split("\n", 3)[3]
    verb = "applied" if a.apply else "would apply"
    print(f"{a.space}: {verb} -- Inbox created: {stats['inbox_created']}, moved into Inbox: {stats['moved_into_inbox']}, archived: {stats['archived']}"
          + (f" -> {target.name}" if arch else ""))
    if not a.apply:
        return 0
    if out == src.read_text() and not arch:
        return 0
    backup = src.with_suffix(".org.pre-cleanup")
    backup.write_text(src.read_text())
    src.write_text(out)
    if arch:
        target.write_text(arch)
    ok = parses(src) and (arch is None or parses(target))
    if not ok:
        src.write_text(backup.read_text())
        if arch and not target.exists():
            pass
        print(f"{a.space}: result did not parse -- inbox.org restored from {backup.name}", file=sys.stderr)
        return 2
    backup.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
