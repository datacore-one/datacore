#!/usr/bin/env python3
"""Canonical date operations for Datacore.

LLMs are bad at day-of-week arithmetic and anchor to training-era dates.
This module is the single source of truth for any date operation — agents
should call it instead of typing dates from memory.

CLI:
    date_utils.py today                    # 2026-04-08 Wed
    date_utils.py today --iso              # 2026-04-08
    date_utils.py today --full             # 2026-04-08 Wed 14:32 CET
    date_utils.py dow 2026-04-08           # Wed
    date_utils.py validate 2026-04-08 Wed  # ok / mismatch
    date_utils.py add 2026-04-08 3         # 2026-04-11 Sat
    date_utils.py sub 2026-04-08 7         # 2026-04-01 Wed
    date_utils.py diff 2026-04-08 2026-04-15   # 7
    date_utils.py parse "next monday"      # resolved against today
    date_utils.py org-stamp 2026-04-08     # <2026-04-08 Wed>
    date_utils.py fix "2026-04-08 Tue foo" # 2026-04-08 Wed foo

Library:
    from date_utils import today, dow, validate, add_days, org_stamp
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Optional

DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
DATE_DOW_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)")
DOWS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _parse_iso(s: str) -> date:
    m = DATE_RE.match(s)
    if not m:
        raise ValueError(f"not a YYYY-MM-DD date: {s!r}")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))


def today() -> date:
    return date.today()


def dow(d: str | date) -> str:
    """Return 3-letter day-of-week for a date (Mon..Sun)."""
    if isinstance(d, str):
        d = _parse_iso(d)
    return d.strftime("%a")


def validate(d: str, day_name: str) -> bool:
    """True if day_name matches the actual day-of-week for d."""
    return dow(d) == day_name


def add_days(d: str | date, n: int) -> date:
    if isinstance(d, str):
        d = _parse_iso(d)
    return d + timedelta(days=n)


def diff_days(a: str | date, b: str | date) -> int:
    if isinstance(a, str):
        a = _parse_iso(a)
    if isinstance(b, str):
        b = _parse_iso(b)
    return (b - a).days


def org_stamp(d: str | date, inactive: bool = False) -> str:
    """Format a date as an org-mode timestamp: <2026-04-08 Wed>."""
    if isinstance(d, str):
        d = _parse_iso(d)
    stamp = f"{d.isoformat()} {d.strftime('%a')}"
    return f"[{stamp}]" if inactive else f"<{stamp}>"


def parse_relative(expr: str, base: Optional[date] = None) -> date:
    """Resolve simple relative expressions against base (default: today).

    Supported: today, tomorrow, yesterday,
               next/last {mon..sun|week|month},
               in N days, N days ago,
               YYYY-MM-DD (passthrough).
    """
    if base is None:
        base = today()
    s = expr.strip().lower()

    if DATE_RE.match(s):
        return _parse_iso(s[:10])
    if s == "today":
        return base
    if s == "tomorrow":
        return base + timedelta(days=1)
    if s == "yesterday":
        return base - timedelta(days=1)

    m = re.match(r"in\s+(\d+)\s+days?", s)
    if m:
        return base + timedelta(days=int(m.group(1)))
    m = re.match(r"(\d+)\s+days?\s+ago", s)
    if m:
        return base - timedelta(days=int(m.group(1)))

    m = re.match(r"(next|last)\s+(mon|tue|wed|thu|fri|sat|sun)", s)
    if m:
        direction = 1 if m.group(1) == "next" else -1
        target = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"].index(m.group(2))
        delta = (target - base.weekday()) % 7
        if delta == 0:
            delta = 7
        return base + timedelta(days=delta * direction if direction > 0 else -((7 - delta) % 7 or 7))

    if s == "next week":
        return base + timedelta(days=7)
    if s == "last week":
        return base - timedelta(days=7)

    raise ValueError(f"cannot parse relative date: {expr!r}")


def fix_day_names(text: str) -> tuple[str, int]:
    """Fix wrong day-of-week names in text. Returns (fixed_text, n_fixes)."""
    fixes = 0

    def repl(m: re.Match) -> str:
        nonlocal fixes
        date_str, day_name = m.group(1), m.group(2)
        try:
            actual = dow(date_str)
            if actual != day_name:
                fixes += 1
                return f"{date_str} {actual}"
        except ValueError:
            pass
        return m.group(0)

    return DATE_DOW_RE.sub(repl, text), fixes


def find_mismatches(text: str) -> list[dict]:
    """Find all date/dow mismatches in text. Returns list of {date, claimed, actual, line}."""
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for m in DATE_DOW_RE.finditer(line):
            date_str, claimed = m.group(1), m.group(2)
            try:
                actual = dow(date_str)
                if actual != claimed:
                    out.append({
                        "line": i,
                        "date": date_str,
                        "claimed": claimed,
                        "actual": actual,
                    })
            except ValueError:
                pass
    return out


# --- CLI ---

def _cmd_today(args: list[str]) -> str:
    d = today()
    if "--iso" in args:
        return d.isoformat()
    if "--full" in args:
        now = datetime.now().astimezone()
        return f"{d.isoformat()} {d.strftime('%a')} {now.strftime('%H:%M %Z')}"
    return f"{d.isoformat()} {d.strftime('%a')}"


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0

    cmd = argv[0]
    rest = argv[1:]

    try:
        if cmd == "today":
            print(_cmd_today(rest))
        elif cmd == "dow":
            print(dow(rest[0]))
        elif cmd == "validate":
            d, day = rest[0], rest[1]
            actual = dow(d)
            if actual == day:
                print(f"ok: {d} is {day}")
            else:
                print(f"mismatch: {d} is {actual}, not {day}")
                return 1
        elif cmd == "add":
            d = add_days(rest[0], int(rest[1]))
            print(f"{d.isoformat()} {d.strftime('%a')}")
        elif cmd == "sub":
            d = add_days(rest[0], -int(rest[1]))
            print(f"{d.isoformat()} {d.strftime('%a')}")
        elif cmd == "diff":
            print(diff_days(rest[0], rest[1]))
        elif cmd == "parse":
            d = parse_relative(" ".join(rest))
            print(f"{d.isoformat()} {d.strftime('%a')}")
        elif cmd == "org-stamp":
            print(org_stamp(rest[0], inactive="--inactive" in rest))
        elif cmd == "fix":
            fixed, n = fix_day_names(" ".join(rest))
            print(fixed)
            print(f"# {n} fix(es)", file=sys.stderr)
        elif cmd == "check":
            # Read file, return JSON of mismatches
            text = open(rest[0]).read()
            mismatches = find_mismatches(text)
            print(json.dumps(mismatches, indent=2))
            return 1 if mismatches else 0
        else:
            print(f"unknown command: {cmd}", file=sys.stderr)
            print(__doc__, file=sys.stderr)
            return 2
    except Exception as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
