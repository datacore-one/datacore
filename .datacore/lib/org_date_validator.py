#!/usr/bin/env python3
"""Validate and fix org-mode date timestamps.

Fixes two classes of errors:
1. Wrong day-of-week names (e.g., <2026-03-14 Fri> when it's actually Saturday)
2. Detects suspect wrong-year dates (reports only, doesn't auto-fix)

Usage:
    # Fix day-of-week mismatches in-place:
    python3 org_date_validator.py fix

    # Dry-run (report only):
    python3 org_date_validator.py check

    # Validate a single date string and return correct day:
    python3 -c "from org_date_validator import correct_day; print(correct_day(2026, 3, 14))"
"""

import re
import sys
from datetime import datetime
from pathlib import Path

DATE_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2})\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)')

def correct_day(year: int, month: int, day: int) -> str:
    """Return the correct 3-letter day abbreviation for a date."""
    return datetime(year, month, day).strftime('%a')


def org_date(year: int, month: int, day: int) -> str:
    """Return a correctly formatted org-mode date string like '2026-03-14 Sat'."""
    dt = datetime(year, month, day)
    return dt.strftime('%Y-%m-%d %a')


def validate_file(filepath: Path, fix: bool = False) -> tuple[list, list]:
    """Validate dates in an org file.

    Returns (day_mismatches, year_suspects) where each is a list of
    (line_number, date_str, wrong_day, correct_day, heading) tuples.
    """
    if not filepath.exists():
        return [], []

    content = filepath.read_text()
    lines = content.splitlines()
    day_mismatches = []
    year_suspects = []

    for i, line in enumerate(lines, 1):
        for m in DATE_PATTERN.finditer(line):
            date_str, day_name = m.group(1), m.group(2)
            try:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                actual_day = dt.strftime('%a')

                # Find nearest heading for context
                heading = ''
                for j in range(i - 1, max(0, i - 20), -1):
                    if lines[j - 1].startswith('*'):
                        heading = lines[j - 1].strip()[:80]
                        break

                if actual_day != day_name:
                    day_mismatches.append((i, date_str, day_name, actual_day, heading))

                if dt.year == 2025 and ('SCHEDULED' in line or 'DEADLINE' in line):
                    year_suspects.append((i, date_str, heading))

            except ValueError:
                pass

    if fix and day_mismatches:
        def fix_day(m):
            date_str = m.group(1)
            try:
                dt = datetime.strptime(date_str, '%Y-%m-%d')
                return f'{date_str} {dt.strftime("%a")}'
            except ValueError:
                return m.group(0)

        new_content = DATE_PATTERN.sub(fix_day, content)
        if new_content != content:
            filepath.write_text(new_content)

    return day_mismatches, year_suspects


def scan_all_org_files(data_root: Path = None, fix: bool = False):
    """Scan all org files in all spaces."""
    if data_root is None:
        data_root = Path.home() / 'Data'

    org_files = list(data_root.glob('*/org/*.org'))
    org_files.extend(data_root.glob('org/*.org'))

    total_day = 0
    total_year = 0

    for f in sorted(org_files):
        day_issues, year_issues = validate_file(f, fix=fix)
        if day_issues or year_issues:
            rel = f.relative_to(data_root)
            for line, date, wrong, correct, heading in day_issues:
                print(f'  {rel}:{line}  {date} {wrong} → {correct}')
                if heading:
                    print(f'    {heading}')
            for line, date, heading in year_issues:
                print(f'  {rel}:{line}  {date} (2025 — suspect)')
                if heading:
                    print(f'    {heading}')
            total_day += len(day_issues)
            total_year += len(year_issues)

    action = 'Fixed' if fix else 'Found'
    print(f'\n{action}: {total_day} day mismatches, {total_year} suspect 2025 dates')
    return total_day, total_year


if __name__ == '__main__':
    mode = sys.argv[1] if len(sys.argv) > 1 else 'check'
    fix = mode == 'fix'
    if fix:
        print('Fixing day-of-week mismatches...\n')
    else:
        print('Checking dates (dry run)...\n')
    scan_all_org_files(fix=fix)
