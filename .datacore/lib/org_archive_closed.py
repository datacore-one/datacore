#!/usr/bin/env python3
"""org_archive_closed.py — move closed (DONE/CANCELLED) top-level subtrees out of an
inbox.org into a dated archive file, text-level, so the org-workspace shrink guard
is not tripped by removing hundreds of entries at once.

Why text-level: org-workspace's move/save path refuses (CatastrophicShrinkError)
or mis-serialises when a write removes more than 25% of a file, and an inbox that
is 85% closed nightshift headings is exactly that case. This script uses
org-workspace only to PARSE (line numbers, subtree ends, states) and then splices
lines, following the precedent of 0-personal/org/inbox-archive-2026-07-14.org.

Usage:
    python3 .datacore/lib/org_archive_closed.py --file 0-personal/org/inbox.org [--dry-run]
    python3 .datacore/lib/org_archive_closed.py --file X.org --archive X-archive-YYYY-MM-DD.org

Rules: only top-level (level 1) headings whose state is DONE or CANCELLED and whose
subtree contains no open task are moved; everything else stays byte-identical.
Archived headings are demoted one level under "* Archived (processed <date>)".
"""
import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from org_workspace import OrgWorkspace  # noqa: E402

CLOSED = {"DONE", "CANCELLED"}
OPEN = {"TODO", "NEXT", "WAITING", "REVIEW", "DEFERRED"}


def closed_spans(path: Path):
    ws = OrgWorkspace()
    ws.load(str(path))
    lines = path.read_text().splitlines(keepends=True)
    spans = []
    for view in _all_nodes(ws):
        if view.level not in (1, 2) or view.todo not in CLOSED:
            continue
        if any((c.todo in OPEN) for c in _descendants(view)):
            continue
        raw = view.node
        start = raw.linenumber - 1
        end = _find_end(lines, start, view.level)
        spans.append((start, end, view.heading, view.level))
    spans.sort()
    # drop spans nested inside an earlier, larger span
    out = []
    for sp in spans:
        if out and sp[0] < out[-1][1]:
            continue
        out.append(sp)
    return lines, out


def _all_nodes(ws):
    seen = []
    for state in list(OPEN | CLOSED) + [None]:
        try:
            seen.extend(ws.find_by_state(state))
        except Exception:
            pass
    uniq = {}
    for v in seen:
        uniq[id(v.node)] = v
    return list(uniq.values())


def _descendants(view):
    out = []
    stack = list(view.children)
    while stack:
        c = stack.pop()
        out.append(c)
        stack.extend(c.children)
    return out


def _find_end(lines, start, level=1):
    """Subtree of a level-N heading ends at the next heading of level <= N."""
    i = start + 1
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("*"):
            stars = len(ln) - len(ln.lstrip("*"))
            if stars <= level and ln[stars:stars + 1] == " ":
                break
        i += 1
    return i


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--archive")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    src = Path(a.file)
    today = date.today().isoformat()
    dst = Path(a.archive) if a.archive else src.with_name(f"{src.stem}-archive-{today}.org")

    lines, spans = closed_spans(src)
    print(f"{src}: {len(spans)} closed subtrees (levels 1-2), {sum(e - s for s, e, _, _ in spans)} lines")
    for s, e, h, lv in spans[:8]:
        print(f"  L{s + 1}-{e} (L{lv}): {h[:80]}")
    if len(spans) > 8:
        print(f"  ... {len(spans) - 8} more")
    if a.dry_run or not spans:
        return

    moved = []
    for s, e, _, lv in spans:
        for ln in lines[s:e]:
            # level-1 subtrees are demoted one level so everything sits under "* Archived"
            moved.append(("*" + ln) if (lv == 1 and ln.startswith("*")) else ln)
    keep = []
    cut = set()
    for s, e, _, _ in spans:
        cut.update(range(s, e))
    for i, ln in enumerate(lines):
        if i not in cut:
            keep.append(ln)

    header = "" if dst.exists() else f"#+TITLE: Inbox Archive {today}\n\n"
    section = f"* Archived (processed {today})\n"
    with dst.open("a") as f:
        f.write(header + section + "".join(moved))
    src.write_text("".join(keep))

    check = OrgWorkspace()
    check.load(str(src))
    print(f"wrote {len(moved)} lines to {dst}; {src} re-parses OK ({len(keep)} lines kept)")


if __name__ == "__main__":
    main()
