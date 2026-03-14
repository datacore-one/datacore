#!/usr/bin/env python3
"""PostToolUse hook: auto-fix wrong day-of-week names in org/md files.

Called by Claude Code after Edit/Write on .org and .md files.
Silently fixes day names in-place. Outputs a warning only if fixes were made.
"""

import re
import sys
from datetime import datetime
from pathlib import Path

DATE_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2})\s+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)')


def fix_dates(filepath: str) -> int:
    """Fix wrong day-of-week names in an org file. Returns count of fixes."""
    p = Path(filepath)
    if not p.exists() or p.suffix not in ('.org', '.md'):
        return 0

    content = p.read_text()
    fixes = 0

    def fix_day(m):
        nonlocal fixes
        date_str = m.group(1)
        day_name = m.group(2)
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            actual_day = dt.strftime('%a')
            if actual_day != day_name:
                fixes += 1
                return f'{date_str} {actual_day}'
        except ValueError:
            pass
        return m.group(0)

    new_content = DATE_PATTERN.sub(fix_day, content)
    if fixes > 0:
        p.write_text(new_content)

    return fixes


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(0)

    filepath = sys.argv[1]
    fixes = fix_dates(filepath)
    if fixes > 0:
        print(f"Auto-fixed {fixes} wrong day-of-week name(s) in {Path(filepath).name}")
