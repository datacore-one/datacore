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

# Frontmatter keys are searched ONLY inside the leading `---` block (see
# frontmatter_span). Run over the whole file they paired a body line
# "date: ..." with any later "day: ..." -- a YAML example in a code fence, say --
# and the pre-commit gate refused the commit and `--fix` rewrote the body.
# [ \t]* rather than \s*: \s* ran over the newline and --fix deleted it.
FM_DATE = re.compile(r"^date:[ \t]*(\d{4})-(\d{2})-(\d{2})[ \t]*$", re.M)
FM_DAY = re.compile(r"^(day:[ \t]*)([A-Za-z]{3,9})[ \t]*$", re.M)
FM_CLOSE = re.compile(r"^(?:---|\.\.\.)[ \t]*$", re.M)

ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def correct_names(y: int, m: int, d: int) -> tuple[str, str]:
    """Return (abbrev, full) day names for a date, or raise ValueError."""
    idx = datetime.date(y, m, d).weekday()
    return ABBR[idx], FULL[idx]


def frontmatter_span(text: str) -> tuple[int, int]:
    """(start, end) of the YAML frontmatter body, or (0, 0) if there is none.

    Frontmatter is a first line `---` and everything up to the next line that
    is `---` or `...`. An unclosed block is not frontmatter.
    """
    first, nl, _ = text.partition("\n")
    if not nl or first.rstrip() != "---":
        return 0, 0
    start = len(first) + 1
    close = FM_CLOSE.search(text, start)
    if not close:
        return 0, 0
    return start, close.start()


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
    fm_start, fm_end = frontmatter_span(fixed)
    fm = FM_DATE.search(fixed, fm_start, fm_end) if fm_end else None
    if fm:
        try:
            abbr, full = correct_names(*(int(g) for g in fm.groups()))
        except ValueError:
            abbr = full = None
        if abbr:
            dm = FM_DAY.search(fixed, fm_start, fm_end)
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
    # A ledger checkpoint is the same thing by another name: a rendering of an
    # append-only log, reproducing every stamp exactly as recorded. A typo made
    # on 2026-08-11 and closed since is history, and the item is `dismissed`, so
    # there is no `item.update` that could correct it even in principle. Without
    # this the first checkpoint of 2-datacore could never be committed --
    # converge failed on it, which fails the whole Phase-1 cycle -- and the only
    # ways out would have been to rewrite history or to stop checkpointing.
    "/.datacore/checkpoints/",
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


def _fix_file(p: pathlib.Path):
    """((problems, fixed), wrote) for one file under --fix, or None if unreadable.

    The read and the rewrite happen under the org lock (owner decision Q12,
    2026-09-23): `watch_file` before the read, `write_org_text` (atomic,
    journalled) for the write, one short transaction per file. A plain
    `write_text` here overwrote any adapter commit that landed between the
    read and the write. Imported lazily so the pre-commit check (no --fix)
    never needs org_transaction or org_workspace.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    import org_transaction

    @org_transaction.serialized
    def run():
        org_transaction.watch_file(p)
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        problems, fixed = check_text(text, str(p))
        wrote = bool(problems) and fixed != text
        if wrote:
            org_transaction.write_org_text(p, fixed)
        return (problems, fixed), wrote

    return run()


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
        if fix:
            result = _fix_file(p)
        else:
            try:
                result = check_text(p.read_text(encoding="utf-8"), str(p)), False
            except (OSError, UnicodeDecodeError):
                result = None
        if result is None:
            continue  # binary or unreadable — not our business
        (problems, _), wrote = result
        all_problems.extend(problems)
        if wrote:
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
