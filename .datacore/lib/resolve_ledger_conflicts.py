#!/usr/bin/env python3
"""Resolve only a provable prefix extension of a valid per-writer ledger.

Ambiguous text/Org/configuration conflicts require review. Refused files retain
both Git index stages and their working bytes. Successful resolutions stage only
their own paths; this utility never commits unrelated index entries.
"""
import re
import subprocess
import sys
from pathlib import Path

from ledger.events import from_line, to_line
from ledger.seal import _chain_issue
from org_transaction import serialized, watch_file, write_org_text


class ForkError(RuntimeError):
    pass


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=30)
    if result.returncode:
        raise RuntimeError('Git conflict state could not be read or updated')
    return result.stdout


def unmerged(repo):
    return [name.decode('utf-8') for name in git(repo, 'diff', '--name-only', '-z', '--diff-filter=U').split(b'\0') if name]


def _events(text):
    if not text:
        return []
    if not text.endswith('\n'):
        raise ForkError('incomplete ledger record')
    events = [from_line(line) for line in text.splitlines()]
    if _chain_issue(events):
        raise ForkError('invalid ledger chain')
    return events


def prefix_resolution(ours, theirs, working):
    left, right = _events(ours), _events(theirs)
    if not (ours.startswith(theirs) or theirs.startswith(ours)):
        raise ForkError('divergent ledger histories require manual recovery')
    # A manually edited working copy must not disappear into an index-stage
    # resolution. Only the exact event set from both stages is eligible.
    lines = [line for line in working.splitlines()
             if not re.match(r'^(<<<<<<< |>>>>>>> |=======$)', line)]
    actual = {to_line(from_line(line)) for line in lines}
    expected = {to_line(event) for event in left + right}
    if actual != expected:
        raise ForkError('working copy differs from the conflicted stages')
    return ours if len(ours) >= len(theirs) else theirs


@serialized
def resolve_file(repo, name):
    repo = Path(repo).resolve()
    path = repo / name
    if not re.fullmatch(r'\.datacore/events/[^/]+\.jsonl', name):
        raise ForkError('no lossless automatic merger for this file type')
    if path.is_symlink() or not path.resolve().is_relative_to(repo):
        raise ForkError('conflict path escapes its repository')
    watch_file(path)
    before = path.read_bytes().decode('utf-8')
    ours = git(repo, 'show', ':2:' + name)
    theirs = git(repo, 'show', ':3:' + name)
    merged = prefix_resolution(ours.decode('utf-8'), theirs.decode('utf-8'), before)
    if git(repo, 'show', ':2:' + name) != ours or git(repo, 'show', ':3:' + name) != theirs:
        raise ForkError('Git conflict stages changed during resolution')
    write_org_text(path, merged)


def main(argv=None):
    repos = list(sys.argv[1:] if argv is None else argv)
    if not repos:
        print('Pass explicit repositories to inspect; no default fleet mutation.', file=sys.stderr)
        return 2
    refused = 0
    for repo in repos:
        try:
            files = unmerged(repo)
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            print(f'{repo}: unable to establish conflict state', file=sys.stderr)
            refused += 1
            continue
        for name in files:
            try:
                resolve_file(repo, name)
                git(repo, 'add', '--', name)
                print(f'{repo}: staged preserving resolution for {name}; commit requires review')
            except (OSError, ValueError, TypeError, RuntimeError, subprocess.TimeoutExpired):
                print(f'{repo}: REFUSED {name}; working file and conflict evidence retained')
                refused += 1
    return 1 if refused else 0


if __name__ == '__main__':
    raise SystemExit(main())
