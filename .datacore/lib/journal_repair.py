#!/usr/bin/env python3
"""Repair journal pages that break "one page per day, one briefing on top".

Two repairs, both moving text and never dropping any (DAY-9, MEM-45):

  fold PAGE CANONICAL   a second dated page for a day (e.g. `journal/2026-09-15.md`
                        in a space whose pages live in `notes/journals/`) is
                        appended to the day's one page, then removed. Its
                        frontmatter and a leading date H1 are the only lines
                        not carried over (the page has its own).
  nest PAGE             the briefing is made one section on top: briefing parts
                        written as their own H2 (Good Morning, The World, ...)
                        move inside `## Daily Briefing` as H3, and an H2 that
                        sits above the briefing (e.g. `## Daily Summary`) moves
                        to just below it. When the briefing already holds its
                        parts, a second run's parts are kept under one H3 that
                        says so, one level deeper -- nothing is merged away.

Dry run by default: prints what would change. `--apply` writes through
journal_store (the lock/recovery protocol every journal writer shares).
Writers were fixed at the source on 2026-09-28; this repairs the pages they
already wrote.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from journal_store import read_journal, update_journal  # noqa: E402
from markdown_sections import frontmatter_end, sections  # noqa: E402

BRIEFING = "Daily Briefing"
PARTS = ("Good Morning", "The World", "Your Agenda", "Spaces", "Decisions Due",
         "Horizon", "Proactive Suggestions", "Data's Observation")
SECOND_RUN = "Another briefing run (moved inside the briefing by journal_repair)"


def _demote(text: str, by: int) -> str:
    """Add `by` levels to every ATX heading outside fenced code."""
    out, fence = [], False
    for line in text.splitlines(keepends=True):
        if line.startswith("```"):
            fence = not fence
        elif not fence and re.match(r"#{1,6} ", line):
            line = "#" * by + line
        out.append(line)
    return "".join(out)


def _block(text: str) -> str:
    return text.rstrip("\n") + "\n\n"


def nest(text: str) -> str:
    """Return the page with one briefing section, first among the H2s."""
    secs = sections(text)
    briefs = [s for s in secs if s.level == 2 and s.title == BRIEFING]
    if len(briefs) != 1:
        return text                                    # none to nest into, or needs review
    brief = briefs[0]
    parts = [s for s in secs if s.level == 2 and s.title in PARTS]
    above = [s for s in secs if s.level == 2 and s.start < brief.start and s not in parts]
    if not parts and not above:
        return text
    body = text[brief.start:brief.end]
    has_parts = any(re.search(rf"(?m)^### {re.escape(p)}\s*$", body) for p in PARTS)
    moved = "".join(_block(_demote(text[s.start:s.end], 2 if has_parts else 1)) for s in parts)
    if moved and has_parts:
        moved = f"### {SECOND_RUN}\n\n" + moved
    new_brief = _block(body) + moved + "".join(_block(text[s.start:s.end]) for s in above)
    drop = {(s.start, s.end) for s in parts + above}
    out, pos = [], 0
    for s in secs:
        if s.start < pos:
            continue
        if (s.start, s.end) in drop:
            out.append(text[pos:s.start]); pos = s.end
        elif s is brief:
            out.append(text[pos:s.start]); out.append(new_brief); pos = s.end
    out.append(text[pos:])
    result = "".join(out)
    return result if result.endswith("\n") else result + "\n"


def _body(extra_text: str, day: str) -> str:
    body = extra_text[frontmatter_end(extra_text):]
    return re.sub(rf"\A\s*# {re.escape(day)}\s*\n", "", body).strip("\n")


def fold(extra_text: str, canonical: str | None, day: str) -> str:
    """The canonical page with the second page's content appended."""
    body = _body(extra_text, day)
    base = canonical if canonical is not None else f"---\ndate: {day}\n---\n\n"
    if not body:
        return base
    return _block(base) + body + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fold"); f.add_argument("page", type=Path); f.add_argument("canonical", type=Path)
    n = sub.add_parser("nest"); n.add_argument("page", type=Path)
    for p in (f, n):
        p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "nest":
        before = read_journal(a.page) or ""
        after = nest(before)
        print(f"{a.page.name}: {'unchanged' if after == before else 'briefing nested'}")
        if a.apply and after != before:
            update_journal(a.page, lambda cur: nest(cur or ""))
        return 0
    day = re.search(r"\d{4}-\d{2}-\d{2}", a.page.name)
    if not day or day.group(0) != a.canonical.stem:
        print(f"refusing: {a.page.name} and {a.canonical.name} are not the same day")
        return 2
    extra = read_journal(a.page)
    if extra is None:
        print(f"{a.page}: missing"); return 2
    print(f"{a.page} -> {a.canonical}: {len(extra.splitlines())} line(s) to fold")
    if a.apply:
        update_journal(a.canonical, lambda cur: fold(extra, cur, day.group(0)))
        if _body(extra, day.group(0))[-200:] not in a.canonical.read_text(encoding="utf-8"):
            print("fold not verified; second page retained"); return 1
        a.page.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
