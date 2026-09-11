"""Durable relay retries live outside the directory replaced by inbound sync."""
import hashlib
import json
from pathlib import Path

from agent_stream_store import EventConflict, _content_hash
from file_utils import atomic_write_text, file_lock, fsync_directory

MAX_EVENTS = 10000
MAX_EVENT_BYTES = 1024 * 1024
MAX_BYTES = 64 * 1024 * 1024


def event_path(directory, row):
    return Path(directory) / (hashlib.sha256(row['id'].encode()).hexdigest() + '.json')


def enqueue(directory, row):
    directory = Path(directory)
    text = json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n'
    encoded = text.encode('utf-8')
    if len(encoded) > MAX_EVENT_BYTES:
        raise ValueError('event exceeds relay capacity')
    path = event_path(directory, row)
    with file_lock(directory / 'outbox'):
        if path.exists():
            existing = json.loads(path.read_text(encoding='utf-8'))
            if _content_hash(existing) != _content_hash(row):
                raise EventConflict('queued event ID names different content')
            return
        files = list(directory.glob('*.json'))
        if len(files) >= MAX_EVENTS or sum(file.stat().st_size for file in files) + len(encoded) > MAX_BYTES:
            raise ValueError('relay outbox capacity exceeded')
        atomic_write_text(path, text)


def acknowledge(directory, row):
    directory = Path(directory)
    with file_lock(directory / 'outbox'):
        path = event_path(directory, row)
        if not path.exists():
            return
        if _content_hash(json.loads(path.read_text(encoding='utf-8'))) != _content_hash(row):
            raise EventConflict('queued event changed before acknowledgement')
        path.unlink()
        fsync_directory(directory)


def flush(directory, send, limit=100):
    directory = Path(directory)
    sent = 0
    # Do not hold the outbox lock during network IO. Concurrent flushes may
    # send a retry twice; the relay's shared retry namespace deduplicates it.
    for path in sorted(directory.glob('*.json'))[:limit]:
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            continue
        if not send(row):
            break
        acknowledge(directory, row)
        sent += 1
    return sent
