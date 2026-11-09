#!/usr/bin/env python3
"""PostToolUse hook: auto-fix wrong day-of-week names in org/md files.

Called by Claude Code after Edit/Write on .org and .md files.
Silently fixes day names in-place. Outputs a warning only if fixes were made.

WRITES GO THROUGH THE ORG TRANSACTION (decision G8, 2026-09-23). This used to
read the file, fix it and `write_text` it back with no lock. An adapter call
that committed in between was silently overwritten (replayed in
specs/datacore-lean/findings/org-transaction.md, candidate 5). Now a file that
needs a fix is re-read and rewritten inside `org_transaction.serialized`, with
`watch_file` + `write_org_text` (atomic, journalled), the way inbox_cleanup
does it. A file with nothing to fix takes no lock at all.

This is a LIVE hook in the owner's session, so it stays fast and never raises:
the lock is waited for at most LOCK_TIMEOUT seconds, through
`org_transaction.serialized(timeout=...)` (decision Q12; this used to patch
`org_transaction.file_lock` for the duration of one call). When it is busy, or the
transaction refuses (a retained journal, a concurrent edit), the fix is
skipped with a one-line warning on stderr. The next Edit/Write retries it.
Model: DatacoreSpec/Dates.lean, section "Hook write under the org lock".
"""

import re
import sys
from datetime import datetime
from pathlib import Path

#: Seconds the hook waits for the org transaction lock before skipping.
LOCK_TIMEOUT = 2.0

# The abbreviation must END the token. Without the lookahead, any word that
# merely starts with a day abbreviation after a date was "fixed": on a Thursday,
# "2026-09-24 Monitor" became "2026-09-24 Thuitor" and "Monday" became
# "Thuday" -- silently, in every .org/.md file after every Edit/Write.
# `[ \t]+`, not `\s+`: `\s` crosses a newline, so a date ending one line and
# "Sat with Bob" starting the next were joined and the word replaced.
# `(?![^\W\d_])` = "not followed by any letter", Unicode-aware.
# Proved in specs/datacore-lean (Dates.fix_rw, fix_idem, new_keeps_lines).
DATE_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2})[ \t]+(Mon|Tue|Wed|Thu|Fri|Sat|Sun)(?![^\W\d_])')


def _fix_text(content: str) -> tuple[str, int]:
    """(fixed content, number of day names corrected)."""
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

    return DATE_PATTERN.sub(fix_day, content), fixes


def fix_dates(filepath: str) -> int:
    """Fix wrong day-of-week names in an org/md file. Returns count of fixes.

    Never raises for a lock or transaction problem: it skips the fix, prints
    one warning line to stderr, and returns 0.
    """
    p = Path(filepath)
    if not p.exists() or p.suffix not in ('.org', '.md'):
        return 0

    # Fast path, no lock: most edits have nothing to fix.
    if _fix_text(p.read_text())[1] == 0:
        return 0

    try:
        import org_transaction

        # The live hook waits LOCK_TIMEOUT, not the batch default (Q12).
        @org_transaction.serialized(timeout=LOCK_TIMEOUT)
        def locked() -> int:
            org_transaction.watch_file(p)
            # Re-read INSIDE the lock: the unlocked read above may be stale.
            current = org_transaction.read_text(p.resolve())
            if current is None:
                return 0
            fixed, n = _fix_text(current)
            if n:
                org_transaction.write_org_text(p, fixed)
            return n

        return locked()
    except Exception as exc:  # noqa: BLE001 -- a live hook must never raise
        reason = "org lock busy" if isinstance(exc, TimeoutError) else type(exc).__name__
        print(f"org_date_hook: skipped day-name fix in {p.name} ({reason}: {exc})",
              file=sys.stderr)
        return 0


if __name__ == '__main__':
    filepath = None
    if len(sys.argv) >= 2:
        filepath = sys.argv[1]
    else:
        # Read PostToolUse hook JSON from stdin
        import json
        try:
            raw = sys.stdin.read(1024 * 1024)
            data = json.loads(raw) if raw.strip() else {}
            filepath = (
                data.get("tool_input", {}).get("file_path")
                or data.get("tool_input", {}).get("file")
            )
        except Exception:
            pass

    if not filepath:
        sys.exit(0)

    try:
        fixes = fix_dates(filepath)
    except Exception as exc:  # noqa: BLE001 -- never raise into the session
        print(f"org_date_hook: skipped ({type(exc).__name__}: {exc})", file=sys.stderr)
        sys.exit(0)
    if fixes > 0:
        print(f"Auto-fixed {fixes} wrong day-of-week name(s) in {Path(filepath).name}", file=sys.stderr)
