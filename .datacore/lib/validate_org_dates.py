#!/usr/bin/env python3
"""Reject date stamps whose day-of-week name does not match the date.

Why this exists at commit time rather than only as a PreToolUse hook:
`org_date_prewrite.py` guards Edit/Write. Anything that writes a file another
way — a `python3 - <<EOF` heredoc, `sed -i`, a script, an agent shelling out —
never touches that hook and lands a wrong day name unchallenged. That happened
on 2026-08-27: seven `[2026-08-27 Wed]` stamps were written through a heredoc
and reached the index. 2026-08-27 was a Thursday.

A commit is the one chokepoint every write path passes through, whatever tool
produced the bytes. So the check belongs here as well, not instead.

Checks two forms:

  org timestamps   <2026-08-27 Thu>   [2026-08-27 Thu 12:30]
                   [2026-08-27 Thu 12:30]--[2026-08-27 Thu 15:33]
                   <2026-08-27 Thu +1w>

  md frontmatter   date: 2026-08-27
                   day:  Thu            (or Thursday)

Deliberately narrow: the day name must sit inside a bracket immediately after
an ISO date. Bare prose is never matched, so "Monitor issue #360" does not
trip on "Mon" — a real false positive from an earlier ad-hoc grep.

Usage:
    validate_org_dates.py FILE [FILE...]     # explicit files
    validate_org_dates.py --staged           # staged .org/.md in this repo
    validate_org_dates.py --fix FILE ...     # rewrite wrong names in place

Exit 0 = clean, 1 = at least one wrong stamp (or a bad file), 2 = usage error.
"""
from __future__ import annotations

import datetime
import pathlib
import re
import subprocess
import sys

# <2026-08-27 Thu ...>  or  [2026-08-27 Thu ...]
STAMP = re.compile(r"([<\[])(\d{4})-(\d{2})-(\d{2})[ \t]+([A-Za-z]{3,9})\b")

FM_DATE = re.compile(r"^date:\s*(\d{4})-(\d{2})-(\d{2})\s*$", re.M)
FM_DAY = re.compile(r"^(day:\s*)([A-Za-z]{3,9})\s*$", re.M)

ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def correct_names(y: int, m: int, d: int) -> tuple[str, str]:
    """Return (abbrev, full) day names for a date, or raise ValueError."""
    idx = datetime.date(y, m, d).weekday()
    return ABBR[idx], FULL[idx]


def check_text(text: str, path: str) -> tuple[list[str], str]:
    """Return (problems, corrected_text)."""
    problems: list[str] = []
    lines = text.splitlines()

    def line_of(pos: int) -> int:
        return text.count("\n", 0, pos) + 1

    # --- org-style stamps -------------------------------------------------
    def fix_stamp(m: re.Match) -> str:
        open_ch, ys, ms, ds, name = m.groups()
        try:
            abbr, full = correct_names(int(ys), int(ms), int(ds))
        except ValueError:
            problems.append(f"{path}:{line_of(m.start())}: impossible date {ys}-{ms}-{ds}")
            return m.group(0)
        if name == abbr or name == full:
            return m.group(0)
        # Only flag things that are actually day names; anything else in that
        # slot is someone's syntax we do not understand, and guessing is worse
        # than passing it through.
        if name not in ABBR and name not in FULL:
            return m.group(0)
        problems.append(
            f"{path}:{line_of(m.start())}: {ys}-{ms}-{ds} is {abbr}, not {name}"
        )
        return f"{open_ch}{ys}-{ms}-{ds} {abbr if len(name) <= 3 else full}"

    fixed = STAMP.sub(fix_stamp, text)

    # --- markdown frontmatter (date: / day:) ------------------------------
    fm = FM_DATE.search(fixed)
    if fm:
        try:
            abbr, full = correct_names(*(int(g) for g in fm.groups()))
        except ValueError:
            abbr = full = None
        if abbr:
            dm = FM_DAY.search(fixed)
            if dm and dm.group(2) not in (abbr, full):
                if dm.group(2) in ABBR or dm.group(2) in FULL:
                    problems.append(
                        f"{path}:{fixed.count(chr(10), 0, dm.start()) + 1}: "
                        f"frontmatter day: {dm.group(2)} but date: "
                        f"{'-'.join(fm.groups())} is {abbr}"
                    )
                    want = abbr if len(dm.group(2)) <= 3 else full
                    fixed = fixed[: dm.start()] + f"{dm.group(1)}{want}" + fixed[dm.end():]

    del lines
    return problems, fixed


# Archived material records what was written at the time, mistakes included.
# Correcting it would rewrite history rather than prevent an error, and the
# bulk of existing wrong stamps live in exactly these paths — 83 of them in one
# 6-meridian orphan snapshot. Live files are the ones worth guarding.
ARCHIVE_MARKERS = (
    "/4-archive/",
    "/archive/",
    "_archive",
    "-archive-",
    "/orphan-snapshot-",
)


def is_archived(rel: str) -> bool:
    r = "/" + rel.replace("\\", "/")
    return any(marker in r for marker in ARCHIVE_MARKERS)


def staged_files() -> list[pathlib.Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True, text=True,
    ).stdout
    root = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True
    ).stdout.strip()
    return [
        pathlib.Path(root) / f
        for f in out.splitlines()
        if f.endswith((".org", ".md"))
        and not is_archived(f)
        and (pathlib.Path(root) / f).is_file()
    ]


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    fix = "--fix" in argv

    if "--staged" in argv:
        targets = staged_files()
    elif args:
        targets = [pathlib.Path(a) for a in args]
    else:
        print(__doc__)
        return 2

    all_problems: list[str] = []
    repaired: list[str] = []
    for p in targets:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # binary or unreadable — not our business
        problems, fixed = check_text(text, str(p))
        if problems:
            all_problems.extend(problems)
            if fix and fixed != text:
                p.write_text(fixed, encoding="utf-8")
                repaired.append(str(p))

    if not all_problems:
        return 0

    print("Wrong day-of-week name(s):", file=sys.stderr)
    for line in all_problems:
        print(f"  {line}", file=sys.stderr)

    if fix:
        for p in repaired:
            print(f"  fixed: {p}", file=sys.stderr)
        print("\nRe-stage the fixed files and commit again.", file=sys.stderr)
        return 1

    print(
        "\nDates were typed rather than computed. Fix with:\n"
        "  python3 .datacore/lib/validate_org_dates.py --fix <file>\n"
        "or get the correct stamp from:\n"
        "  python3 .datacore/lib/date_utils.py dow <YYYY-MM-DD>",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
