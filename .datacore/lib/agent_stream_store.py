"""One durable, locked persistence path for local and relayed agent events."""
import json
import hashlib
import re
from pathlib import Path

from file_utils import atomic_write_text, file_lock

MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_RETAINED_BYTES = 256 * 1024 * 1024
MAX_RETAINED_FILES = 3660


class EventConflict(ValueError):
    """A retry identifier was reused for different event content."""


def append_events(path: Path, rows: list[dict]) -> int:
    """Commit a whole batch or preserve the previous file; deduplicate retries.

    A malformed existing log requires explicit recovery. Never convert a
    read/parse error into an empty stream. IDs are stable retry identifiers.
    """
    # Date rotation must not rotate away retry identity. Local and relay
    # callers serialize against one directory lock across all retained days.
    with file_lock(path.parent / 'agent-stream'):
        try:
            with path.open("rb") as source:
                raw = source.read(MAX_LOG_BYTES + 1)
            if len(raw) > MAX_LOG_BYTES:
                raise ValueError("agent log capacity exceeded; archive it before retrying")
            previous = raw.decode("utf-8")
        except FileNotFoundError:
            previous = ""
        paths = sorted(path.parent.glob('events-????-??-??.jsonl')) if re.fullmatch(r'events-\d{4}-\d{2}-\d{2}\.jsonl', path.name) else []
        paths = [other for other in paths if other != path]
        if len(paths) > MAX_RETAINED_FILES:
            raise ValueError('agent stream retention capacity exceeded; archive old logs')
        known = {}
        total = len(previous.encode('utf-8'))

        def remember(text):
            for line in text.splitlines():
                row = json.loads(line)
                if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
                    raise ValueError('invalid existing agent event')
                content = _content_hash(row)
                if row['id'] in known and known[row['id']] != content:
                    raise EventConflict('existing event ID names different content')
                known[row['id']] = content

        remember(previous)
        for other in paths:
            with other.open('rb') as source:
                data = source.read(min(MAX_LOG_BYTES, MAX_RETAINED_BYTES - total) + 1)
            total += len(data)
            if len(data) > MAX_LOG_BYTES or total > MAX_RETAINED_BYTES:
                raise ValueError('agent stream retention capacity exceeded; archive old logs')
            remember(data.decode('utf-8'))
        additions = []
        for row in rows:
            if not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError("agent event ID must be a nonempty string")
            content = _content_hash(row)
            if row["id"] in known and known[row["id"]] != content:
                raise EventConflict("event ID already names different content")
            if row["id"] not in known:
                additions.append(json.dumps(row, ensure_ascii=False, allow_nan=False))
                known[row["id"]] = content
        if additions:
            prefix = previous + ("\n" if previous and not previous.endswith("\n") else "")
            text = prefix + "\n".join(additions) + "\n"
            if len(text.encode("utf-8")) > MAX_LOG_BYTES:
                raise ValueError("agent log capacity exceeded; archive it before retrying")
            if total + len(("\n".join(additions) + "\n").encode('utf-8')) > MAX_RETAINED_BYTES:
                raise ValueError('agent stream retention capacity exceeded; archive old logs')
            atomic_write_text(path, text)
        return len(additions)


def _content_hash(row):
    data = {key: value for key, value in row.items() if key != 'ts'}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')).digest()
