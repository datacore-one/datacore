#!/usr/bin/env python3
"""Install the space pre-commit hook into every space repo. Idempotent.

Symlinks <space>/.git/hooks/pre-commit -> ../../../.datacore/hooks/space-pre-commit
(relative, so the same install works on the Mac and the nightshift server).

Never overwrites a foreign hook: if a pre-commit already exists and is not
ours, it is reported and skipped. Re-run weekly by nightshift Phase 9.5.

Context: before 2026-06-10 only the root Data repo had pre-commit
enforcement; the space repos had none (structural drift audit finding).
"""

import os
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parents[2]
HOOK_SOURCE = DATA_DIR / '.datacore' / 'hooks' / 'space-pre-commit'
REL_TARGET = os.path.join('..', '..', '..', '.datacore', 'hooks',
                          'space-pre-commit')


def main() -> int:
    if not HOOK_SOURCE.exists():
        print(f"[space-hooks] hook source missing: {HOOK_SOURCE}",
              file=sys.stderr)
        return 1
    HOOK_SOURCE.chmod(0o755)

    installed, skipped, foreign = [], [], []
    for space in sorted(DATA_DIR.glob('[0-9]-*')):
        hooks_dir = space / '.git' / 'hooks'
        if not hooks_dir.is_dir():
            continue  # not a git repo (or worktree-style) — skip
        dest = hooks_dir / 'pre-commit'
        if dest.is_symlink():
            if os.readlink(dest) == REL_TARGET:
                skipped.append(space.name)
                continue
            foreign.append(f"{space.name} (symlink to {os.readlink(dest)})")
            continue
        if dest.exists():
            foreign.append(f"{space.name} (existing non-symlink hook)")
            continue
        dest.symlink_to(REL_TARGET)
        installed.append(space.name)

    print(f"[space-hooks] installed: {len(installed)} "
          f"{installed if installed else ''}")
    if skipped:
        print(f"[space-hooks] already installed: {len(skipped)}")
    for f in foreign:
        print(f"[space-hooks] SKIPPED foreign hook: {f} — review manually")
    return 0


if __name__ == '__main__':
    sys.exit(main())
