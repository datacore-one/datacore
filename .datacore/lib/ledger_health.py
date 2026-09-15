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

from ledger.verify import verify_chain, check_not_rewound
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
        errors = verify_chain(path, registry_path=registry)
        if errors and all('signature verification failed' in error for error in errors):
            raise ValueError('signature verification unresolved')
        witness_errors = check_not_rewound(path)
        if any(not error.startswith('TRUNCATED:') for error in witness_errors):
            raise ValueError('ledger witness unavailable')
        errors += witness_errors
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError('ledger changed during verification')
        return not errors
    finally:
        os.close(fd)


def check(root):
    result = {'version': 1, 'ok': None, 'spaces_verified': 0,
              'spaces_broken': 0, 'spaces_unverified': 0, 'reason': 'no-ledger'}
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
        try:
            files, marks = _files(events, '.jsonl'), _files(witnesses, '.seq')
            if any(path.is_symlink() or not path.is_file() for path in marks):
                raise ValueError('ledger witness is not a regular local file')
            if not events.exists() and not marks:
                continue
            unverified = bool({p.stem for p in marks} - {p.stem for p in files})
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
        except (OSError, ValueError):
            result['spaces_unverified'] += 1
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
    args = parser.parse_args()
    print(json.dumps(check(args.root), sort_keys=True))


if __name__ == '__main__':
    main()
