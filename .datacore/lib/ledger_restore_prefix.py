#!/usr/bin/env python3
"""Restore a per-writer ledger log from a PROVABLE prefix extension.

The park machinery preserves uncommitted work on a branch before resetting a
space, which is exactly what it should do. What nothing does is merge that
branch back — so events sit on a park branch while the writer's sequence
high-water mark stays ahead of the log, and every later append is refused with
StaleLogError. Measured 2026-09-16: eleven events across two spaces, stranded
since 2026-09-08, with the nightshift writer unable to append to either space
for eight days.

Finding them is the hard part and it is easy to get wrong. A first search of
working copies, origin/main and dangling objects concluded they were
UNRECOVERABLE; they were on a park branch the whole time, which only
`git rev-list --all` reveals. Acting on that wrong conclusion -- clearing the
witness mark, or DATACORE_HWM_OVERRIDE=1 -- would have written new events over
those sequence numbers and made a recoverable situation permanent. So `--find`
searches every commit in every history, and nothing here writes without proof.

The proof required before any write, both conditions, no exceptions:

  PREFIX    the current log is a byte-for-byte prefix of the recovered one, so
            the restore only ever APPENDS. A recovered log that diverges is a
            forked chain and belongs to a human.
  CHAIN     the recovered log's hash chain verifies end to end.

    ledger_restore_prefix.py --space 0-personal --actor nightshift --find
    ledger_restore_prefix.py --space 0-personal --actor nightshift --from <rev> --apply
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
sys.path.insert(0, str(LIB))


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ''


def log_path(space: Path, actor: str) -> str:
    return f'.datacore/events/{actor}.jsonl'


def tail_seq(text: str) -> int | None:
    import json
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return None
    try:
        return int(json.loads(lines[-1])['seq'])
    except Exception:  # noqa: BLE001 -- a malformed tail is simply not a candidate
        return None


def find(space: Path, actor: str, since: str) -> list[tuple[str, int]]:
    """Every commit in EVERY history whose copy of this log runs further."""
    rel = log_path(space, actor)
    current = tail_seq((space / rel).read_text(encoding='utf-8'))
    out = []
    for commit in git(space, 'rev-list', '--all', f'--since={since}', '--', rel).split():
        seq = tail_seq(git(space, 'show', f'{commit}:{rel}'))
        if seq is not None and current is not None and seq > current:
            subject = git(space, 'log', '-1', '--format=%s', commit).strip()
            refs = git(space, 'branch', '-a', '--contains', commit).strip().replace('\n', ', ')
            out.append((commit, seq, subject, refs))
    return sorted(out, key=lambda r: -r[1])


def restore(space: Path, actor: str, rev: str, apply: bool) -> int:
    from ledger.events import from_line
    from ledger.seal import _chain_issue
    rel = log_path(space, actor)
    path = space / rel
    current = path.read_text(encoding='utf-8')
    recovered = git(space, 'show', f'{rev}:{rel}')

    if not recovered:
        print(f'REFUSED — {rev} has no {rel}')
        return 1
    if not recovered.startswith(current):
        print('REFUSED — the recovered log is NOT a prefix extension of the current one.\n'
              '          That is a forked chain, not a restore, and belongs to a human.')
        return 1
    if len(recovered) == len(current):
        print('nothing to restore; the logs are identical')
        return 0
    events = [from_line(line) for line in recovered.splitlines() if line.strip()]
    issue = _chain_issue(events)
    if issue:
        print(f'REFUSED — the recovered chain does not verify: {issue}')
        return 1

    added = [e.seq for e in events[len(current.splitlines()):]]
    print(f'{space.name}/{actor}: {len(current.splitlines())} -> {len(events)} events')
    print(f'  prefix extension: yes | chain verifies: yes | would add: {added}')
    if not apply:
        print('  dry run — pass --apply to write')
        return 0

    backup = path.with_name(path.name + '.pre-restore')
    shutil.copy2(path, backup)
    path.write_text(recovered, encoding='utf-8')
    after = [from_line(l) for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
    if _chain_issue(after):
        shutil.copy2(backup, path)
        print('  post-write chain broken — ORIGINAL RESTORED, nothing changed')
        return 1
    print(f'  restored; chain verifies; previous log kept at {backup.name}')
    # This tool does not commit, so whoever runs it commits ANOTHER principal's
    # writer log under their own git identity -- which is exactly what DIP-0044's
    # authorship check exists to catch, and it duly fails. Committing as the
    # principal is not available: principals.yaml stores email hashes, not
    # addresses. So say the follow-up out loud. Measured 2026-09-17: four such
    # commits left `bridge-v2-verify` REGRESSED on winston, unnoticed, until a
    # contract sweep found it a day later.
    print(f'  NEXT: commit this, then record the commit in '
          f'.datacore/config/authorship-reviewed.yaml as writer {actor!r} in '
          f'{space.name} -- the DIP-0044 authorship check fails until you do.')
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--space', required=True, type=Path)
    ap.add_argument('--actor', required=True)
    ap.add_argument('--find', action='store_true', help='search every history for a longer log')
    ap.add_argument('--since', default='2026-01-01')
    ap.add_argument('--from', dest='rev', help='the commit to restore from')
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args(argv)

    space = a.space.resolve()
    if a.find:
        rows = find(space, a.actor, a.since)
        if not rows:
            print('no commit in any history holds a longer log')
            return 0
        for commit, seq, subject, refs in rows:
            print(f'{commit[:9]}  tail seq {seq}  {subject[:50]}')
            print(f'           refs: {refs or "(unreferenced)"}')
        return 0
    if not a.rev:
        ap.error('--from is required unless --find')
    return restore(space, a.actor, a.rev, a.apply)


if __name__ == '__main__':
    sys.exit(main())
