#!/usr/bin/env python3
"""DIP-0009 v2.0 one-time rollout: canonical header + state migration.

For every live org file ([0-9]-*/org/*.org, archives excluded):
  - replace any #+SEQ_TODO line with the v2.0 canon (insert at top if absent)
  - migrate heading state keywords:
      QUEUED->NEXT  WORKING->NEXT  FAILED->REVIEW  PAUSED->WAITING
      PROJECT->TODO ACTIVE->NEXT   COMPLETED->DONE ASSIGN->TODO
      EXECUTING->NEXT

Header rewrites and keyword swaps are plain text edits (sanctioned by the
harmonization plan); everything stateful stays in org_workspace-land.

Dry-run by default. --execute applies; --commit also does, per space:
git pull --no-rebase, commit, push (a failed push is reported, never fatal —
gitea-remoted spaces sync later via transport).

DO NOT run while nightshift-overnight is active — the runner rewrites the
same files and one side's edit loses.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

CANON = ("#+SEQ_TODO: TODO(t) NEXT(n!) WAITING(w!) REVIEW(r!) "
         "| DONE(d!) DEFERRED(f!) CANCELLED(c!)")
STATE_MAP = {
    'QUEUED': 'NEXT', 'WORKING': 'NEXT', 'EXECUTING': 'NEXT',
    'FAILED': 'REVIEW', 'PAUSED': 'WAITING',
    'PROJECT': 'TODO', 'ACTIVE': 'NEXT', 'COMPLETED': 'DONE',
    'ASSIGN': 'TODO',
}
_HEAD = re.compile(r'^(\*+\s+)(' + '|'.join(STATE_MAP) + r')(\s)')


def _git(repo: Path, *args, check=False):
    return subprocess.run(['git', '-C', str(repo), *args],
                          capture_output=True, text=True, check=check)


def migrate_file(fp: Path, execute: bool) -> dict:
    text = fp.read_text(encoding='utf-8')
    lines = text.split('\n')
    header_fixed = header_present = False
    swaps: dict = {}
    for i, line in enumerate(lines):
        if line.startswith('#+SEQ_TODO:'):
            header_present = True
            if line.strip() != CANON:
                lines[i] = CANON
                header_fixed = True
            continue
        m = _HEAD.match(line)
        if m:
            old = m.group(2)
            lines[i] = m.group(1) + STATE_MAP[old] + line[m.end(2):]
            swaps[old] = swaps.get(old, 0) + 1
    if not header_present:
        lines.insert(0, CANON)
        header_fixed = True
    changed = header_fixed or bool(swaps)
    if changed and execute:
        fp.write_text('\n'.join(lines), encoding='utf-8')
    return {'file': fp, 'changed': changed, 'header': header_fixed,
            'swaps': swaps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data-dir', default=str(Path.home() / 'Data'))
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--commit', action='store_true',
                    help='per space: pull --no-rebase, commit, push (implies --execute)')
    a = ap.parse_args()
    execute = a.execute or a.commit
    root = Path(a.data_dir)

    by_space: dict = {}
    for fp in sorted(root.glob('[0-9]-*/org/*.org')):
        if 'archive' in fp.name.lower():
            continue
        by_space.setdefault(fp.parts[len(root.parts)], []).append(fp)

    total_files = total_swaps = 0
    for space, files in by_space.items():
        space_dir = root / space
        if a.commit:
            r = _git(space_dir, 'pull', '--no-rebase', '-q', 'origin',
                     _git(space_dir, 'branch', '--show-current').stdout.strip() or 'main')
            if r.returncode != 0:
                print(f"{space}: PULL FAILED — skipping this space entirely "
                      f"({(r.stderr or '').strip()[:100]})")
                continue
        changed_files = []
        for fp in files:
            res = migrate_file(fp, execute)
            if res['changed']:
                changed_files.append(fp)
                total_files += 1
                total_swaps += sum(res['swaps'].values())
                swaps = ' '.join(f"{k}->{STATE_MAP[k]}×{v}"
                                 for k, v in res['swaps'].items())
                print(f"{'' if execute else '[dry-run] '}{fp.relative_to(root)}"
                      f": {'header ' if res['header'] else ''}{swaps}")
        if a.commit and changed_files:
            rels = [str(f.relative_to(space_dir)) for f in changed_files]
            _git(space_dir, 'add', '--', *rels)
            c = _git(space_dir, 'commit', '-q',
                     '-m', 'org: DIP-0009 v2.0 state-loop rollout — canonical '
                           'header + QUEUED/WORKING/FAILED/PROJECT migration',
                     '--', *rels)
            if c.returncode != 0:
                print(f"{space}: COMMIT REJECTED — "
                      f"{(c.stdout or c.stderr).strip()[:200]}")
                continue
            p = _git(space_dir, 'push', '-q')
            print(f"{space}: committed"
                  + ("" if p.returncode == 0 else
                     " — push failed (transport will sync later)"))

    print(f"\nrollout: {total_files} file(s) changed, {total_swaps} heading(s) migrated"
          + ("" if execute else " (dry-run — nothing written)"))
    return 0


if __name__ == '__main__':
    sys.exit(main())
