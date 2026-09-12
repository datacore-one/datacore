#!/usr/bin/env python3
"""Inject memory using the installed CLI, with private per-session exclusion.

The async hook has a 90-second budget; the child gets 85 seconds and foreground
process-group cleanup. Session reminders are advisory, never authorization.
"""
import argparse
import fcntl
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hook_state import state_path
import plur_cli
import process_run

_TIMEOUT = 85
_MAX_STDIN = 512 * 1024
_MAX_SESSION = 4096


def _hook_cmd():
    return plur_cli.command('hook-inject')


def _run_hook(stdin_data, timeout=_TIMEOUT, rehydrate=False):
    try:
        command = _hook_cmd() + (['--rehydrate'] if rehydrate else [])
        result = process_run.run(
            command, input=stdin_data, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, timeout=timeout,
        )
        return result.stdout if result.returncode == 0 else ''
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ''


def _try_lock(session_id):
    """Lock a private hashed path without following links or truncating data.

    Keep the inode after unlocking: unlinking a held lock can let subsequent
    callers lock different inodes and both enter the protected operation.
    """
    fd = None
    try:
        if not isinstance(session_id, str) or len(session_id) > _MAX_SESSION:
            return None
        path = state_path('plur-inject', session_id)
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise ValueError('PLUR inject lock must be private regular state')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = os.fdopen(fd, 'r+')
        fd = None
        return result
    except (OSError, ValueError, UnicodeError):
        return None
    finally:
        if fd is not None:
            os.close(fd)


def _session_marked(session_id):
    # PLUR's legacy advisory sentinel uses raw UUIDs. Only accept the bounded
    # ASCII safe subset, avoiding traversal and sanitization collisions. Never
    # follow a link or rely on another user's marker. This does not grant tools.
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', session_id):
        return False
    try:
        info = (Path(tempfile.gettempdir()) / f'plur-session-{session_id}').lstat()
        return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                and info.st_nlink == 1 and not info.st_mode & 0o022)
    except OSError:
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rehydrate', action='store_true')
    args = parser.parse_args(argv)
    try:
        stdin_data = sys.stdin.read(_MAX_STDIN + 1)
        if len(stdin_data) > _MAX_STDIN:
            raise ValueError('hook input is too large')
        data = json.loads(stdin_data) if stdin_data.strip() else {}
        if not isinstance(data, dict):
            raise ValueError('hook input must be an object')
        session_id = data.get('session_id', '')
        if not isinstance(session_id, str) or len(session_id) > _MAX_SESSION:
            raise ValueError('invalid session identity')
    except (ValueError, EOFError, RecursionError):
        print('{}')
        return

    lock = _try_lock(session_id)
    stdout = ''
    if lock is not None:
        try:
            stdout = _run_hook(stdin_data, rehydrate=args.rehydrate)
        finally:
            lock.close()
    try:
        output = json.loads(stdout) if stdout.strip() else {}
        if not isinstance(output, dict):
            output = {}
    except (ValueError, RecursionError):
        output = {}

    if session_id and not _session_marked(session_id) and not os.environ.get('DATACORE_HEADLESS'):
        existing = output.get('additionalContext', '')
        if not isinstance(existing, str):
            existing = ''
        reminder = 'Call plur_session_start before beginning work to load session memory.'
        output['additionalContext'] = f'{reminder}\n\n{existing}' if existing else reminder
    print(json.dumps(output))


if __name__ == '__main__':
    def terminate(signum, _frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, terminate)
    main()
