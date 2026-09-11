#!/usr/bin/env python3
"""Merge valid cadence index stages without guessing when evidence is missing."""
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
import yaml

from org_transaction import serialized, watch_file, write_org_text
from git_conflict import conflict_stages


def stage(repo, name, number):
    result = subprocess.run(['git', '-C', str(repo), 'show', f':{number}:{name}'], capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError('required conflict stage is unavailable')
    document = yaml.safe_load(result.stdout)
    if not isinstance(document, dict) or any(not isinstance(key, str) or not isinstance(row, dict) for key, row in document.items()):
        raise ValueError('cadence stage must be a map of records')
    return document, result.stdout


def merge(ours, theirs):
    result = dict(ours)
    for key, row in theirs.items():
        if key not in result:
            result[key] = row
            continue
        old = result[key]
        if old == row:
            continue
        def stamp(value):
            parsed = datetime.fromisoformat(str(value['last_run']))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        before, after = stamp(old), stamp(row)
        if before == after:
            raise ValueError('different cadence records have the same timestamp')
        # Keep fields not modeled by an older writer while respecting newer
        # explicit field values. Both original versions remain in Git stages.
        result[key] = {**old, **row} if after > before else {**row, **old}
    return result


@serialized
def resolve(repo, name):
    repo = Path(repo).resolve()
    path = repo / name
    if path.is_symlink() or not path.resolve().is_relative_to(repo):
        raise ValueError('cadence path escapes its repository')
    watch_file(path)
    before = path.read_bytes()
    conflict_stages(repo, name, before)
    ours, left = stage(repo, name, 2)
    theirs, right = stage(repo, name, 3)
    text = yaml.safe_dump(merge(ours, theirs), default_flow_style=False, sort_keys=True, allow_unicode=True)
    if stage(repo, name, 2)[1] != left or stage(repo, name, 3)[1] != right:
        raise ValueError('cadence conflict stages changed')
    write_org_text(path, text)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print('Pass one repository-relative cadence log path', file=sys.stderr)
        return 2
    result = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=30)
    if result.returncode:
        return 2
    try:
        resolve(Path(result.stdout.strip()), args[0])
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, yaml.YAMLError, subprocess.TimeoutExpired):
        print('REFUSED: cadence evidence is missing, ambiguous or changed; originals retained', file=sys.stderr)
        return 1
    print('Resolved cadence working file; Git index stages retained for review')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
