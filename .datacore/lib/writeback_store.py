"""Prepared file writes: durable intent, atomic publication, retry reconciliation.

The queue keeps the exact before hash and complete after text before a file
changes. A crash after publication but before queue acknowledgement is safe:
retry recognizes the already-applied bytes rather than appending them again.
Org transaction recovery handles interrupted publication before this protocol
examines the file. A third version is a conflict, never an overwrite target.
"""
from contextlib import closing
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import zettel_db
from org_literal import scalar
from org_transaction import serialized, watch_file, write_org_text


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def target_path(space, value):
    if space is not None and space not in zettel_db.SPACES:
        raise ValueError('unknown write-back space')
    root = Path(zettel_db.SPACES[space]['path'] if space is not None else zettel_db.DATA_ROOT).resolve()
    path = Path(value)
    path = (path if path.is_absolute() else root / path).resolve()
    if not path.is_relative_to(root) or path.suffix.lower() not in {'.org', '.md'}:
        raise ValueError('write-back target is outside the configured content space')
    return path


def ensure_plans(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS writeback_plans (
        write_id INTEGER PRIMARY KEY, before_sha256 TEXT NOT NULL,
        after_text TEXT NOT NULL, target_file TEXT NOT NULL)''')


def heading_match(text, heading, extra_state=''):
    if not isinstance(heading, str) or not heading.strip() or len(heading.splitlines()) != 1:
        raise ValueError('a unique complete heading is required')
    states = {'TODO', 'NEXT', 'WAITING', 'HOLD', 'DONE', 'CANCELLED', 'REVIEW', 'DEFERRED', 'STARTED', 'IN-PROGRESS'}
    if extra_state:
        states.add(extra_state)
    state_pattern = '|'.join(re.escape(value) for value in sorted(states, key=len, reverse=True))
    pattern = re.compile(r'^(\*+ )(?:(?P<state>' + state_pattern + r')\s+)?(?P<title>[^\r\n]*)(?P<ending>\r?\n)?$')
    lines = text.splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        match = pattern.fullmatch(line)
        if not match:
            continue
        title = re.sub(r'^\[#[A-Z]\]\s*', '', match['title'])
        title = re.sub(r'\s+:[\w@#%:.-]+:\s*$', '', title).strip()
        if title == heading.strip():
            matches.append((index, match))
    if len(matches) != 1:
        raise ValueError('heading is missing or ambiguous; no file was changed')
    index, match = matches[0]
    return lines, index, match


def render(text, operation, changes):
    if not isinstance(changes, dict):
        raise ValueError('write-back changes must be an object')
    if operation == 'append':
        content = changes.get('content')
        if not isinstance(content, str):
            raise ValueError('append content must be text')
        return text + content
    if operation == 'update_state':
        old, new = changes.get('old_state', ''), changes.get('new_state')
        if not isinstance(old, str) or (old and not re.fullmatch(r'[A-Z][A-Z0-9_-]*', old)):
            raise ValueError('invalid expected task state')
        if not isinstance(new, str) or not re.fullmatch(r'[A-Z][A-Z0-9_-]*', new):
            raise ValueError('invalid new task state')
        lines, index, match = heading_match(text, changes.get('heading'), old)
        actual = match['state'] or ''
        if actual not in {old, new}:
            raise ValueError('task state changed since the operation was prepared')
        if actual != new:
            lines[index] = match[1] + new + ' ' + match['title'] + (match['ending'] or '')
        return ''.join(lines)
    if operation == 'update_property':
        key, value = changes.get('property'), changes.get('new_value')
        if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_@#%+.-]+', key):
            raise ValueError('invalid Org property name')
        value = scalar(value)
        lines, index, _ = heading_match(text, changes.get('heading'))
        start = end = None
        for number in range(index + 1, len(lines)):
            line = lines[number].strip()
            if re.match(r'^\*+ ', lines[number]):
                break
            if line == ':PROPERTIES:':
                if start is not None:
                    raise ValueError('ambiguous property drawer')
                start = number
            elif line == ':END:' and start is not None:
                end = number
                break
        if start is None or end is None:
            raise ValueError('complete property drawer is required')
        pattern = re.compile(r'^(\s*:' + re.escape(key) + r':)[ \t]*(.*?)(\r?\n)?$', re.I)
        matches = [number for number in range(start + 1, end) if pattern.fullmatch(lines[number])]
        if len(matches) > 1:
            raise ValueError('ambiguous existing property')
        ending = '\r\n' if lines[end].endswith('\r\n') else '\n'
        if matches:
            number = matches[0]
            match = pattern.fullmatch(lines[number])
            lines[number] = match[1] + ' ' + value + (match[3] or '')
        else:
            lines.insert(end, f'  :{key}: {value}{ending}')
        return ''.join(lines)
    raise ValueError('unsupported write-back operation')


@serialized
def queue(space, table_name, record_id, target_file, operation, changes=None):
    target = target_path(space, target_file)
    watch_file(target)
    before = target.read_bytes().decode('utf-8')
    after = render(before, operation, changes or {})
    with closing(zettel_db.get_connection(space)) as conn, conn:
        ensure_plans(conn)
        cursor = conn.execute('''INSERT INTO pending_writes
            (table_name, record_id, operation, changes, target_file, status)
            VALUES (?, ?, ?, ?, ?, 'pending')''',
            (table_name, record_id, operation, json.dumps(changes), str(target)))
        identity = cursor.lastrowid
        conn.execute('INSERT INTO writeback_plans VALUES (?, ?, ?, ?)',
                     (identity, sha(before), after, str(target)))
    return identity


@serialized
def _apply(identity, space):
    with closing(zettel_db.get_connection(space)) as conn, conn:
        ensure_plans(conn)
        write = conn.execute('SELECT * FROM pending_writes WHERE id = ?', (identity,)).fetchone()
        if write is None:
            return False, 'queued write not found', None
        if write['status'] == 'completed':
            return True, 'already completed', None
        if write['status'] != 'pending':
            return False, 'queued write requires conflict/failure review', None
        target = target_path(space, write['target_file'])
        watch_file(target)
        current = target.read_bytes().decode('utf-8')
        plan = conn.execute('SELECT * FROM writeback_plans WHERE write_id = ?', (identity,)).fetchone()
        if plan is None:
            # A re-index may postdate the old queue entry or a partial apply.
            # It cannot reconstruct that operation's original precondition.
            conn.execute("UPDATE pending_writes SET status='conflict', error_message=? WHERE id=?",
                         ('legacy write has no enqueue-time source snapshot', identity))
            return False, 'legacy write requires source review before resubmission', None
        if plan['target_file'] != str(target):
            raise ValueError('write-back plan target changed')
        after = plan['after_text']
        if sha(current) not in {plan['before_sha256'], sha(after)}:
            conn.execute("UPDATE pending_writes SET status='conflict', error_message=? WHERE id=?",
                         ('source changed after write was queued', identity))
            return False, 'source changed after write was queued', None
        # The prepared intent must already be durable before publication.
        conn.commit()
        if current != after:
            write_org_text(target, after)
        return True, 'file applied', (target, after)


@serialized
def _finish(identity, space, applied):
    target, after = applied
    now = datetime.now().isoformat()
    with closing(zettel_db.get_connection(space)) as conn, conn:
        # A later legitimate edit must not receive this older index checksum.
        if target.read_bytes().decode('utf-8') == after:
            checksum = hashlib.md5(after.replace('\r\n', '\n').encode()).hexdigest()
            conn.execute('''INSERT OR REPLACE INTO file_checksums
                (path, checksum, indexed_at, modified_at) VALUES (?, ?, ?, ?)''',
                (str(target), checksum, now, now))
        conn.execute("UPDATE pending_writes SET status='completed', applied_at=?, error_message=NULL WHERE id=?", (now, identity))


def process(identity, space):
    ok, message, applied = _apply(identity, space)
    # _apply's Org transaction is fully committed before acknowledging the DB.
    # If this step fails, its durable plan reconciles the retry without replay.
    if ok and applied is not None:
        _finish(identity, space, applied)
    return ok, message


@serialized
def update_file(path, operation, changes):
    path = Path(path)
    watch_file(path)
    before = path.read_bytes().decode('utf-8')
    try:
        after = render(before, operation, changes)
    except ValueError as error:
        return False, str(error)
    if after != before:
        write_org_text(path, after)
    return True, 'file updated'
