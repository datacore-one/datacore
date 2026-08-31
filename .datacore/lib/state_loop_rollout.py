#!/usr/bin/env python3
"""DIP-0009 v2.0 one-time rollout: canonical header + state migration.

For every live org file ([0-9]-*/org/*.org, archives excluded):
  - replace any #+SEQ_TODO line with the v2.0 canon (insert at top if absent)
  - migrate heading state keywords:
      QUEUED->NEXT  WORKING->NEXT  FAILED->REVIEW  PAUSED->WAITING
      PROJECT->TODO ACTIVE->NEXT   COMPLETED->DONE ASSIGN->TODO
      EXECUTING->NEXT

`--ledger` migrates the OTHER copy of the same state: the value stored in
each live item's ledger payload, which projector.py renders verbatim.
Migrating only the files left the projection emitting `WORKING`/`QUEUED`
headings under a header that no longer declares them. Append-only —
`item.update` events, never a log rewrite.

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


def migrate_ledger(space: Path, execute: bool) -> dict:
    """Migrate retired state keywords stored INSIDE ledger event payloads.

    The org files are only half the store. A live item's state also lives in
    its ledger payload, and `projector.py` renders that value verbatim -- so
    migrating the files while leaving the payloads produced a projection
    emitting 164 `WORKING` and 3 `QUEUED` headings under a `#+SEQ_TODO` that
    (correctly, per v2.0) no longer declares either. A generated file whose
    keywords it does not declare does not parse standalone: org reads
    `* WORKING Fix the thing` as a heading TITLED "WORKING Fix the thing"
    with no state at all, which is exactly the silent-loss class the
    SEQ_TODO line was added to prevent.

    APPEND-ONLY. This emits `item.update` events carrying the new state; it
    never rewrites history. Rewriting the log to "clean up" the old values
    would break every hash chain behind them (DIP-0046) and destroy the
    record that the item was ever in that state -- which is the audit trail
    the ledger exists to keep.

    Closed items are skipped: the projector already renders those as
    DONE/CANCELLED from `status`, never from the payload, so their stored
    state is history and correctly stays untouched.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ledger.fold import fold
    from ledger.log import EventLog, read_events

    try:
        state = fold(read_events(space))
    except Exception as exc:                      # noqa: BLE001
        return {'space': space.name, 'error': str(exc), 'swaps': {}}

    live = ('created', 'claimed', 'granted')
    todo: list[tuple[str, str, str]] = []
    for iid, item in state.items.items():
        if item.status not in live:
            continue
        old = (item.payload or {}).get('state')
        if old in STATE_MAP:
            todo.append((iid, old, STATE_MAP[old]))

    swaps: dict = {}
    for _, old, _new in todo:
        swaps[old] = swaps.get(old, 0) + 1

    if todo and execute:
        log = EventLog(space, _actor())
        for iid, old, new in todo:
            log.append('item.update', {
                'id': iid, 'state': new,
                'reason': f'DIP-0009 v2.0 state migration: {old} -> {new}',
            })
    return {'space': space.name, 'swaps': swaps, 'count': len(todo)}


def _actor() -> str:
    import socket
    return socket.gethostname().split('.')[0]


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
    ap.add_argument('--ledger', action='store_true',
                    help='migrate retired states stored in ledger payloads '
                         '(append-only item.update events), not org files')
    a = ap.parse_args()
    execute = a.execute or a.commit
    root = Path(a.data_dir)

    if a.ledger:
        total = 0
        for space_dir in sorted(root.glob('[0-9]-*')):
            if not (space_dir / '.datacore' / 'events').is_dir():
                continue
            res = migrate_ledger(space_dir, execute)
            if res.get('error'):
                print(f"{res['space']}: SKIPPED — {res['error'][:120]}")
                continue
            if res['swaps']:
                total += res['count']
                swaps = ' '.join(f"{k}->{STATE_MAP[k]}×{v}"
                                 for k, v in sorted(res['swaps'].items()))
                print(f"{'' if execute else '[dry-run] '}{res['space']}: {swaps}")
        print(f"\nledger: {total} item(s) migrated"
              + ("" if execute else " (dry-run — no events appended)"))
        return 0

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
