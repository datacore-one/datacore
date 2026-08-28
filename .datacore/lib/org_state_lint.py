#!/usr/bin/env python3
"""DIP-0009 v2.0 state-loop lint.

Checks live org files ([0-9]-*/org/*.org, archives excluded) for:
  1. header drift — every #+SEQ_TODO must equal the canon exactly
  2. retired state keywords on headings (QUEUED WORKING FAILED PAUSED ASSIGN
     PROJECT ACTIVE COMPLETED EXECUTING)
  3. (warning) DEFERRED without a wake trigger — no SCHEDULED: line and no
     lane-referencing :PARK_REASON: — such an item belongs in someday.org

Exit 1 on violations (1, 2); warnings alone exit 0.
Usage: org_state_lint.py [--data-dir DIR]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

CANON = ("#+SEQ_TODO: TODO(t) NEXT(n!) WAITING(w!) REVIEW(r!) "
         "| DONE(d!) DEFERRED(f!) CANCELLED(c!)")
RETIRED = ('QUEUED', 'WORKING', 'FAILED', 'PAUSED', 'ASSIGN',
           'PROJECT', 'ACTIVE', 'COMPLETED', 'EXECUTING')
_HEAD = re.compile(r'^(\*+)\s+([A-Z]+)\s')


def _live_org_files(root: Path):
    for fp in sorted(root.glob('[0-9]-*/org/*.org')):
        if 'archive' in fp.name.lower():
            continue
        yield fp


def lint(root: Path) -> int:
    violations, warnings = [], []
    for fp in _live_org_files(root):
        rel = fp.relative_to(root)
        try:
            lines = fp.read_text(encoding='utf-8', errors='replace').split('\n')
        except OSError as exc:
            violations.append(f"{rel}: unreadable ({exc})")
            continue
        saw_header = False
        deferred_line = None
        has_wake = False
        for i, line in enumerate(lines, 1):
            if line.startswith('#+SEQ_TODO:'):
                saw_header = True
                if line.strip() != CANON:
                    violations.append(f"{rel}:{i}: non-canonical #+SEQ_TODO")
                continue
            m = _HEAD.match(line)
            if m:
                # close out the previous DEFERRED block's wake check
                if deferred_line and not has_wake:
                    warnings.append(
                        f"{rel}:{deferred_line}: DEFERRED without SCHEDULED: "
                        f"or lane :PARK_REASON: — belongs in someday.org")
                deferred_line, has_wake = None, False
                state = m.group(2)
                if state in RETIRED:
                    violations.append(f"{rel}:{i}: retired keyword {state}")
                elif state == 'DEFERRED':
                    deferred_line = i
            elif deferred_line:
                if ('SCHEDULED:' in line
                        or re.search(r':PARK_REASON:.*\S+:\S+', line)):
                    has_wake = True
        if deferred_line and not has_wake:
            warnings.append(
                f"{rel}:{deferred_line}: DEFERRED without SCHEDULED: or lane "
                f":PARK_REASON: — belongs in someday.org")
        if not saw_header:
            violations.append(f"{rel}: missing #+SEQ_TODO header")

    for v in violations:
        print(f"VIOLATION {v}")
    for w in warnings:
        print(f"warning   {w}")
    print(f"org-state-lint: {len(violations)} violation(s), "
          f"{len(warnings)} warning(s)")
    return 1 if violations else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default=str(Path.home() / 'Data'))
    a = ap.parse_args()
    return lint(Path(a.data_dir))


if __name__ == '__main__':
    sys.exit(main())
