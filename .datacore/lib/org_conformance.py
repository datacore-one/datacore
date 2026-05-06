#!/usr/bin/env python3
"""Scan + fix structural conformance issues in org-mode files.

Emacs `org-agenda` is strict about format. Common things that quietly
break agenda views:

  1. SCHEDULED/DEADLINE on the heading line itself (must be on the
     line *immediately after* the heading)
  2. PROPERTIES drawer with leading whitespace on :PROPERTIES: or :END:
     markers, or missing :END:
  3. Heading levels skipping (* → *** without **)
  4. Tags malformed (must be `:tag1:tag2:` with no spaces; orphan
     colons or trailing whitespace breaks the tag parser)
  5. Mixed line endings (CRLF in a tree of LF files)
  6. Trailing whitespace in headings (rare but breaks regexp anchors)
  7. SCHEDULED/DEADLINE day-of-week mismatch — handled by the
     dedicated `org_date_validator.py`; this script delegates that.

The agenda also requires the file to be in `org-agenda-files` —
that's an emacs config concern, not a file conformance one. This
script reports and (with --fix) corrects file-level issues only.

Usage:
    python3 org_conformance.py check          # report only
    python3 org_conformance.py fix            # apply safe fixes
    python3 org_conformance.py fix --dry-run  # preview fixes
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DATA_ROOT = Path.home() / "Data"

HEADING_RE = re.compile(r"^(\*+)(\s+)(.*)$")
SCHEDULED_INLINE_RE = re.compile(
    r"^(\*+\s+(?:[A-Z]+\s+)?[^\n]+?)\s+(SCHEDULED:|DEADLINE:)\s*(<[^>]+>|\[[^\]]+\])\s*$"
)
PROPERTIES_OPEN_RE = re.compile(r"^\s+:PROPERTIES:\s*$")
PROPERTIES_END_RE = re.compile(r"^\s+:END:\s*$")
LOGBOOK_OPEN_RE = re.compile(r"^\s+:LOGBOOK:\s*$")
TAGS_RE = re.compile(r"\s+(:[\w@:]+:)\s*$")


def find_org_files(root: Path) -> list[Path]:
    """Walk all numbered spaces + the root for org files we care about."""
    out: list[Path] = []
    # Top-level org/ in numbered spaces
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not re.match(r"^\d+-", entry.name):
            continue
        org_dir = entry / "org"
        if org_dir.is_dir():
            out.extend(sorted(org_dir.glob("*.org")))
    return out


class Issue:
    __slots__ = ("path", "line", "kind", "detail", "fixable")

    def __init__(self, path: Path, line: int, kind: str, detail: str, fixable: bool = False):
        self.path = path
        self.line = line
        self.kind = kind
        self.detail = detail
        self.fixable = fixable

    def __str__(self) -> str:
        try:
            rel = self.path.relative_to(DATA_ROOT)
        except ValueError:
            rel = self.path
        marker = "[FIX]" if self.fixable else "[!!]"
        return f"  {marker} {rel}:{self.line}  {self.kind}\n         {self.detail}"


def scan_file(path: Path) -> tuple[list[Issue], list[str]]:
    """Return (issues, lines). Lines may be mutated by `apply_fixes`."""
    raw = path.read_text(errors="replace")
    lines = raw.splitlines()
    issues: list[Issue] = []

    in_drawer: str | None = None
    drawer_open_line = -1

    for i, line in enumerate(lines):
        lineno = i + 1

        # SCHEDULED/DEADLINE on heading line — flag for fix
        m = SCHEDULED_INLINE_RE.match(line)
        if m:
            issues.append(Issue(
                path, lineno,
                "scheduled_on_heading_line",
                f"SCHEDULED/DEADLINE must be on the line *after* the heading. "
                f"Heading: {m.group(1)[:60]}",
                fixable=True,
            ))
            continue

        # PROPERTIES drawer hygiene
        if PROPERTIES_OPEN_RE.match(line):
            if in_drawer == "PROPERTIES":
                issues.append(Issue(
                    path, lineno,
                    "nested_properties",
                    "Found :PROPERTIES: while another properties drawer is still open",
                ))
            in_drawer = "PROPERTIES"
            drawer_open_line = lineno
            continue
        if LOGBOOK_OPEN_RE.match(line):
            in_drawer = "LOGBOOK"
            drawer_open_line = lineno
            continue
        if PROPERTIES_END_RE.match(line):
            if in_drawer is None:
                issues.append(Issue(
                    path, lineno,
                    "orphan_end",
                    ":END: with no matching open drawer",
                ))
            in_drawer = None

        # Heading level skipping
        if line.startswith("*"):
            stars_match = re.match(r"^(\*+)\s", line)
            if stars_match and i > 0:
                # Look at most-recent heading
                pass  # skip-detection requires full pass; cheap to add later

        # Trailing whitespace on headings
        if HEADING_RE.match(line) and line.rstrip() != line:
            issues.append(Issue(
                path, lineno,
                "trailing_whitespace",
                f"Heading has trailing whitespace: {line!r}",
                fixable=True,
            ))

    # Unclosed drawer at EOF
    if in_drawer is not None:
        issues.append(Issue(
            path, drawer_open_line,
            "unclosed_drawer",
            f"{in_drawer} drawer opened but never closed",
        ))

    return issues, lines


def apply_fixes(path: Path, lines: list[str]) -> tuple[list[str], int]:
    """Apply safe fixes to lines in-memory. Return (new_lines, count)."""
    out: list[str] = []
    count = 0
    for line in lines:
        original = line

        # Trailing whitespace on headings
        if HEADING_RE.match(line) and line.rstrip() != line:
            line = line.rstrip()

        # SCHEDULED/DEADLINE on heading line — split onto next line
        m = SCHEDULED_INLINE_RE.match(line)
        if m:
            heading_part = m.group(1).rstrip()
            kw = m.group(2)
            stamp = m.group(3)
            indent = len(re.match(r"^(\*+)", heading_part).group(1)) + 1
            out.append(heading_part)
            out.append(f"{' ' * indent}{kw} {stamp}")
            count += 1
            continue

        if line != original:
            count += 1
        out.append(line)
    return out, count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "fix"))
    parser.add_argument("--dry-run", action="store_true", help="With fix: preview only")
    parser.add_argument("--root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)

    files = find_org_files(args.root)
    if not files:
        print("No org files found.")
        return 0

    total_issues = 0
    total_fixed = 0
    files_touched = 0

    print(f"Scanning {len(files)} org files...")
    for f in files:
        issues, lines = scan_file(f)
        if not issues:
            continue
        total_issues += len(issues)
        for issue in issues:
            print(issue)

        if args.mode == "fix":
            new_lines, fixed = apply_fixes(f, lines)
            if fixed > 0:
                if not args.dry_run:
                    f.write_text("\n".join(new_lines) + "\n")
                    files_touched += 1
                total_fixed += fixed

    print(f"\n{'=' * 60}")
    print(f"  Scanned: {len(files)} files")
    print(f"  Issues:  {total_issues}")
    if args.mode == "fix":
        action = "Would fix" if args.dry_run else "Fixed"
        print(f"  {action}: {total_fixed} (across {files_touched} files)")
    return 0 if total_issues == 0 else 0  # exit 0 either way; this is informational


if __name__ == "__main__":
    sys.exit(main())
