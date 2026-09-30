#!/usr/bin/env python3
"""Is today's briefing in the journal, and is it a real one?

Prints one of: NO_FILE, NO_BRIEFING, STUB, EXISTS. Used by /today step 2.

STUB is a `## Daily Briefing` without its narrative sections, e.g. Winston's
fallback when the writer's draft was withheld (2026-09-30: only a raw "Facts"
list). /today regenerates a STUB in place instead of treating it as done.

Usage: briefing_state.py [journal.md]   (default: today's personal journal)
"""
from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

NARRATIVE = ("### Good Morning", "### The World")


def briefing_state(journal: Path) -> str:
    journal = Path(journal)
    if not journal.exists():
        return "NO_FILE"
    text = journal.read_text(encoding="utf-8")
    m = re.search(r"^## Daily Briefing\b", text, re.M)
    if not m:
        return "NO_BRIEFING"
    nxt = re.search(r"^## (?!#)", text[m.end():], re.M)
    section = text[m.start(): m.end() + nxt.start()] if nxt else text[m.start():]
    return "EXISTS" if all(re.search(rf"^{re.escape(h)}\b", section, re.M) for h in NARRATIVE) else "STUB"


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path.home() / "Data" / "0-personal" / "notes" / "journals" / f"{date.today().isoformat()}.md")
    print(briefing_state(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
