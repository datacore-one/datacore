"""Private operational metadata for observation hooks; never capture tool content.

Version 2 keeps event/tool/outcome and pseudonymous session/workspace groups.
Legacy raw observations remain on disk but are excluded from automatic learning.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from datetime import datetime, timedelta, timezone

EVENTS = {'PreToolUse', 'PostToolUse', 'PostToolUseFailure'}
TOOLS = {'Bash', 'Edit', 'Write', 'Agent', 'Skill', 'WebFetch', 'WebSearch',
         'NotebookEdit', 'EnterPlanMode', 'ExitPlanMode', 'AskUserQuestion', 'External'}
SKIP_TOOLS = {'Read', 'Glob', 'Grep', 'TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList',
              'TaskOutput', 'TaskStop', 'ToolSearch'}
MAX_FILE = 10 * 1024 * 1024
MAX_RECORD = 2048


def directory():
    return Path(os.environ.get('PLUR_PATH', Path.home() / '.plur')) / 'observations'


def _token(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def record(data, event, session_id, workspace):
    if not isinstance(data, dict) or not isinstance(event, str) or event not in EVENTS:
        return None
    tool = data.get('tool_name') or data.get('name')
    if not isinstance(tool, str) or not tool or tool in SKIP_TOOLS:
        return None
    if not isinstance(session_id, str) or len(session_id) > 4096:
        session_id = ''
    result = {'schema': 2, 'ts': datetime.now(timezone.utc).isoformat(), 'event': event,
              'tool': tool if tool in TOOLS else 'External',
              'session_id': _token(session_id) if session_id else '',
              'workspace': _token(str(workspace))}
    if event != 'PreToolUse':
        result['success'] = event == 'PostToolUse'
    return result


def validated(value):
    """Decode only this metadata schema; fabricated fields never enter learning."""
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value['schema'] != 2:
        return None
    expected = {'schema', 'ts', 'event', 'tool', 'session_id', 'workspace'}
    event = value.get('event')
    if (not isinstance(event, str) or event not in EVENTS
            or not isinstance(value.get('tool'), str) or value['tool'] not in TOOLS):
        return None
    if event != 'PreToolUse':
        expected.add('success')
        if value.get('success') is not (event == 'PostToolUse'):
            return None
    if set(value) != expected:
        return None
    for name in ('session_id', 'workspace'):
        v = value.get(name)
        if not isinstance(v, str) or not re.fullmatch(r'[0-9a-f]{64}' + ('|' if name == 'session_id' else ''), v):
            return None
    try:
        stamp = value['ts']
        if not isinstance(stamp, str) or len(stamp) > 40:
            return None
        if datetime.fromisoformat(stamp).tzinfo is None:
            return None
    except ValueError:
        return None
    return dict(value)


def _directory_fd(root, create=False):
    root = Path(root)
    if create:
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise ValueError('observation directory has a different owner')
        # Contain legacy files without rewriting or removing their contents.
        if create:
            os.fchmod(fd, 0o700)
        elif info.st_mode & 0o077:
            raise ValueError('observation directory must be private')
        return fd
    except BaseException:
        os.close(fd)
        raise


def append(root, value):
    if validated(value) is None:
        raise ValueError('invalid observation metadata')
    content = (json.dumps(value, separators=(',', ':')) + '\n').encode('utf-8')
    if len(content) > MAX_RECORD:
        raise ValueError('observation metadata too large')
    directory_fd = _directory_fd(root, create=True)
    fd = None
    try:
        name = datetime.now(timezone.utc).strftime('%Y-%m-%d') + '.metadata.jsonl'
        fd = os.open(name, os.O_CREAT | os.O_APPEND | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                     0o600, dir_fd=directory_fd)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError('observation file must be private regular state')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        size = os.fstat(fd).st_size
        # Retain an interrupted final record but delimit it before publishing
        # the next one, so a partial tail cannot swallow acknowledged metadata.
        if size and os.pread(fd, 1, size - 1) != b'\n':
            content = b'\n' + content
        if size + len(content) > MAX_FILE:
            raise ValueError('daily observation metadata limit reached')
        remaining = memoryview(content)
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError('observation write made no progress')
            remaining = remaining[written:]
        os.fsync(fd)
        os.fsync(directory_fd)
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory_fd)


def load(root, days):
    """Read bounded metadata files only, without consuming legacy raw logs."""
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError('observation window must be between 1 and 90 days')
    try:
        directory_fd = _directory_fd(root)
    except FileNotFoundError:
        return []
    result = []
    bytes_read = 0
    try:
        today = datetime.now(timezone.utc).date()
        for offset in range(days):
            name = (today - timedelta(days=offset)).isoformat() + '.metadata.jsonl'
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            except FileNotFoundError:
                continue
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                        or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > MAX_FILE):
                    raise ValueError('unsafe observation metadata file')
                while line := stream.readline(MAX_RECORD + 1):
                    bytes_read += len(line)
                    if bytes_read > 16 * 1024 * 1024 or len(result) >= 50000:
                        raise ValueError("observation analysis limit reached")
                    if len(line) > MAX_RECORD:
                        raise ValueError('oversized observation metadata record')
                    try:
                        value = validated(json.loads(line))
                    except (ValueError, RecursionError):
                        value = None
                    if value is not None:
                        result.append(value)
    finally:
        os.close(directory_fd)
    return sorted(result, key=lambda item: item['ts'])
