#!/usr/bin/env python3
"""Read-only, content-free ledger verification for installed runtime clients."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ledger.log import TELEMETRY_DIR
from ledger.verify import verify_log, _rewind_problems, verdict_of
from spaces import discover_spaces


def _files(directory, suffix):
    if directory.resolve() != directory.absolute():
        raise ValueError('ledger directory crosses its space')
    try:
        entries = list(directory.iterdir())
    except FileNotFoundError:
        return []
    if directory.is_symlink():
        raise ValueError('ledger directory is an alias')
    return sorted(path for path in entries if path.name.endswith(suffix))


def verify_chain(path, registry_path=None):
    """One log's classified problems, from the one verifier (incremental)."""
    return list(verify_log(path, registry_path=registry_path, incremental=True).problems)


def _verify_file(path, registry):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError('ledger entry is not regular')
        # An active append is not proof of a broken chain. Report unverified
        # instead of waiting indefinitely or parsing a writer's partial record.
        fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if registry.resolve() != registry.absolute():
            raise ValueError('verification registry crosses its installation')
        # The one verifier (ledger.verify, Phase 2 V2): the CLI, the relay
        # guard, the seal and the checkpoint judge the same events the same
        # way. A problem this machine cannot judge (a writer whose verify key
        # it lacks, an unreadable witness) is unverified, never broken (V5).
        problems = verify_chain(path, registry_path=registry) + _rewind_problems(path)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('ledger changed during verification')
        verdict = verdict_of(problems)
        if verdict == 'unknown':
            raise ValueError('ledger could not be judged on this machine')
        return verdict == 'ok'
    finally:
        os.close(fd)


def check(root, per_space=None):
    """Content-free verdict over every space's ledger.

    `per_space` (a list) receives one (space name, ok | broken | could not check)
    per space with a ledger -- for the `--report` mode, never in the JSON.
    """
    result = {'version': 1, 'ok': None, 'spaces_verified': 0,
              'spaces_broken': 0, 'spaces_unverified': 0, 'reason': 'no-ledger'}
    note = per_space.append if per_space is not None else (lambda row: None)
    root = Path(root)
    if not root.is_absolute() or not root.is_dir():
        return dict(result, reason='discovery-unverified')
    root = root.resolve()
    try:
        spaces = discover_spaces(root, reject_aliases=True, reject_invalid=True)
    except (OSError, ValueError):
        return dict(result, reason='discovery-unverified')
    for space in spaces:
        events = space.path / '.datacore/events'
        witnesses = space.path / '.datacore/state/seq-hwm'
        # Task logs and the per-space telemetry logs (decision 3): both history.
        telemetry = space.path / '.datacore' / TELEMETRY_DIR
        try:
            files, marks = _files(events, '.jsonl'), _files(witnesses, '.seq')
            t_files, t_marks = _files(telemetry, '.jsonl'), _files(witnesses / TELEMETRY_DIR, '.seq')
            if any(path.is_symlink() or not path.is_file() for path in marks + t_marks):
                raise ValueError('ledger witness is not a regular local file')
            if not events.exists() and not marks:
                continue
            unverified = bool({p.stem for p in marks} - {p.stem for p in files}
                              or {p.stem for p in t_marks} - {p.stem for p in t_files})
            files = files + t_files
            broken = False
            for path in files:
                try:
                    broken |= not _verify_file(path, root / '.datacore/keys/registry.yaml')
                except (OSError, ValueError):
                    unverified = True
            if broken:
                result['spaces_broken'] += 1
            if unverified:
                result['spaces_unverified'] += 1
            if not broken and not unverified:
                result['spaces_verified'] += 1
            note((space.path.name, 'broken' if broken else 'could not check' if unverified else 'ok'))
        except (OSError, ValueError):
            result['spaces_unverified'] += 1
            note((space.path.name, 'could not check'))
    if result['spaces_broken']:
        result.update(ok=False, reason='broken')
    elif result['spaces_unverified']:
        result['reason'] = 'verification-incomplete'
    elif result['spaces_verified']:
        result.update(ok=True, reason='verified')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--report', action='store_true',
                        help='ledger-only health check: one line per space; exit 0 sound, '
                             '1 broken, 3 could not check (never a pass)')
    args = parser.parse_args()
    if not args.report:
        print(json.dumps(check(args.root), sort_keys=True))
        return
    # The ledger-only health check (audit C7): integrity of every space's
    # ledger and nothing about the fleet -- the thing to alert on.
    rows = []
    result = check(args.root, per_space=rows)
    for name, verdict in rows:
        print(f'  {name}: {verdict}')
    if result['spaces_broken']:
        print(f"FAIL {result['spaces_broken']} space(s) broken")
        sys.exit(1)
    if result['ok'] is not True:
        print(f"NOT CHECKED {result['spaces_unverified']} space(s) could not be checked "
              f"on this machine ({result['reason']})")
        sys.exit(3)
    print(f"OK {result['spaces_verified']} space(s) verified")


if __name__ == '__main__':
    main()
