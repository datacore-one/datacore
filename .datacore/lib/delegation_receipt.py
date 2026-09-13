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
