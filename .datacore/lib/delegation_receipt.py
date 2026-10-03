"""Versioned CoS attempt/completion evidence consumed by delegation executors.

These records coordinate trusted components. Filesystem permissions and actor
credentials must independently prevent a worker from forging review evidence.
"""
from datetime import datetime, timezone
import json
from pathlib import Path

from file_utils import atomic_write_text_within, read_text_within
from module_context import parse_json
from space_catalog import catalog

VERSION = 2


def paths(root):
    root = Path(root).resolve(strict=True)
    selected = [row for row in catalog(root)['spaces'] if row['name'] == 'datacore']
    if len(selected) != 1:
        raise ValueError('canonical delegation receipt space is unavailable')
    directory = root / selected[0]['path'] / '.datacore/reviews'
    return directory / 'cos-lastrun.json', directory / 'cos-lastattempt.json'


def publish(root, path, document):
    if Path(path) not in paths(root):
        raise ValueError('invalid delegation receipt destination')
    atomic_write_text_within(root, path, json.dumps(document, sort_keys=True) + '\n')
    _commit(Path(path))


def _commit(path: Path) -> None:
    """Commit the receipt just written, alone, in its own repository. Never raises.

    THE WRITER SAVES ITS OWN WORK (fleet sim 2026-10-03, break 9). The review
    runs at 05:55 and left both receipts as uncommitted changes in the system
    space; the overnight run at 06:00 found them, rescued them to a branch,
    cleaned the checkout and alerted "Unsaved work rescued in 2-datacore" --
    every morning, and the cleaned checkout no longer held the receipt the
    review had just written. Only this file is committed (`--only`): anything
    another writer has staged stays staged. If the commit cannot be made the
    receipt still stands on disk, the next converge's autosave carries it,
    and the reason is said on stderr.
    """
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items()
           if k not in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_INDEX_FILE', 'GIT_COMMON_DIR')}

    def git(*args):
        return subprocess.run(['git', '-C', str(path.parent), *args], capture_output=True,
                              text=True, timeout=60, env=env)
    try:
        top = git('rev-parse', '--show-toplevel')
        if top.returncode:
            return                                  # not in a checkout: nothing to commit
        if git('check-ignore', '-q', '--', path.name).returncode == 0:
            return                                  # ignored: a local record, by choice
        rel = path.resolve().relative_to(Path(top.stdout.strip()).resolve()).as_posix()
        from ledger_transport import _repo_lock
        with _repo_lock(Path(top.stdout.strip())):
            added = git('add', '--', path.name)
            if added.returncode:
                raise RuntimeError(added.stderr.strip() or 'git add failed')
            if git('diff', '--cached', '--quiet', '--', path.name).returncode == 0:
                return                              # unchanged since the last commit
            done = git('commit', '-q', '--only', '-m', f'cos: delegation review receipt ({path.name})',
                       '--', path.name)
            if done.returncode:
                raise RuntimeError((done.stderr or done.stdout).strip() or 'git commit failed')
    except Exception as exc:  # noqa: BLE001 -- the receipt is written; saving it is best effort
        said = ' '.join(str(exc).split())[:200]
        print(f'delegation receipt {path.name} written but not committed '
              f'({type(exc).__name__}: {said}); the next sync autosaves it', file=sys.stderr)


def fresh_hours(root):
    """Unknown, partial, legacy, future, mismatched or failed review means hold."""
    try:
        success, attempt = paths(root)
        first = read_text_within(root, attempt, limit=65536)
        current = parse_json(first) if first is not None else None
        raw = read_text_within(root, success, limit=65536)
        receipt = parse_json(raw) if raw is not None else None
        if (not isinstance(current, dict) or not isinstance(receipt, dict)
                or current.get('version') != VERSION or receipt.get('version') != VERSION
                or current.get('status') != 'complete' or receipt.get('status') != 'complete'
                or not isinstance(receipt.get('review_id'), str) or not receipt['review_id']
                or not isinstance(receipt.get('actor'), str) or not receipt['actor']
                or any(current.get(key) != receipt.get(key) for key in ('review_id', 'actor', 'ts'))
                or first != read_text_within(root, attempt, limit=65536)):
            return None
        elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(receipt['ts'])).total_seconds() / 3600
        return elapsed if elapsed >= 0 else None
    except (OSError, ValueError, TypeError, KeyError):
        return None
