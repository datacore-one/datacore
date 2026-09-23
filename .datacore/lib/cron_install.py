#!/usr/bin/env python3
"""Reconcile explicitly owned cron jobs, preserving unrelated entries verbatim.

Managed jobs carry stable identifiers. Legacy direct script invocations are
recognized by executable and discriminator arguments, never a substring of a
command. Installation takes a local installer lock, saves a private backup,
checks for intervening edits, and verifies the installed bytes.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shlex
import subprocess

MARKER = '# datacore-job:'
KEY = re.compile(r'[a-z0-9][a-z0-9-]*\Z')
ASSIGNMENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*=')
INTERPRETER = re.compile(r'(python(?:[0-9]+(?:\.[0-9]+)*)?|bash|sh)\Z')


def invocation(line: str, *, reject_compound: bool = False) -> tuple[str, ...] | None:
    """Identify a direct cron command; comments/env declarations aren't jobs."""
    stripped = line.strip()
    if not stripped or stripped.startswith('#') or ASSIGNMENT.match(stripped):
        return None
    parts = stripped.split(None, 5)
    if stripped.startswith('@'):
        parts = stripped.split(None, 1)
        command = parts[1] if len(parts) == 2 else ''
    else:
        if len(parts) != 6:
            return None
        command = parts[5]
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|<>')
    lexer.whitespace_split = True
    tokens = list(lexer)
    if reject_compound and any(t in (';', '&&', '||', '|', '&', ';;', ';&') for t in tokens):
        raise ValueError('compound managed cron command requires explicit reconciliation')
    while tokens and (ASSIGNMENT.match(tokens[0]) or tokens[0] == 'env'):
        tokens.pop(0)
    if not tokens:
        return None
    if INTERPRETER.fullmatch(Path(tokens[0]).name):
        tokens.pop(0)
        while tokens and tokens[0] in ('-u', '-B', '-E', '-s'):
            tokens.pop(0)
    if not tokens or tokens[0].startswith('-'):
        return None
    script = tokens.pop(0)
    program = Path(script).name
    identity = '.datacore/lib/' + program if '/.datacore/lib/' in script else script
    args = []
    for token in tokens:
        if token in (';', '&&', '||', '|', '&', '>', '>>', '<') or token.startswith(('1>', '2>')):
            break
        args.append(token)
    # These commands have independently scheduled jobs sharing an executable.
    if program == 'unit_alive.sh':
        return (identity, args[0] if args else '')
    # atomic_out.sh wraps a producer and is identified by the ARTIFACT it writes;
    # audit_trio_run.sh dispatches on a mode. Without these, five detectors that
    # each write their own artifact were one "ambiguous invocation" and the
    # installer correctly refused all five (2026-09-21) -- it identifies a job by
    # its executable, which is right until several jobs share a wrapper.
    if program in ('atomic_out.sh', 'audit_trio_run.sh'):
        return (identity, args[0] if args else '')
    if program == 'job_verify.py':
        return (identity, args[args.index('--machine') + 1] if '--machine' in args and args.index('--machine') + 1 < len(args) else '')
    return (identity,)


def reconcile(current: str, entries: dict[str, str], retire: tuple[str, ...] = ()) -> str:
    """Return the desired crontab; identifiers must be unique and well formed."""
    desired = {}
    for key, line in entries.items():
        if not KEY.fullmatch(key) or '\n' in line or '\r' in line or not invocation(line):
            raise ValueError('invalid managed cron entry')
        signature = invocation(line, reject_compound=True)
        if signature in desired:
            raise ValueError('ambiguous managed cron invocations')
        desired[signature] = key
    kept = []
    # A crontab LINE ends at '\n' and nowhere else. str.splitlines also splits
    # at \x0b \x0c \x1c-\x1e \x85 \u2028 \u2029, so one unmanaged cron line
    # holding such a byte was judged as two, and a fragment that looked like a
    # managed job was dropped from the middle of it (GitFleet.lean
    # `reconcile_preserves_unmanaged`).
    for line in re.findall(r'[^\n]*\n|[^\n]+\Z', current):
        stripped = line.strip()
        if stripped.startswith('#'):
            kept.append(line)
            continue
        signature = invocation(line)
        marker = re.search(r'# datacore-job:([a-z0-9-]+)\s*$', line)
        if marker and marker.group(1) in entries:
            continue
        if signature in desired or (signature and signature[0] in retire):
            invocation(line, reject_compound=True)
            continue
        kept.append(line)
    output = ''.join(kept)
    if output and not output.endswith('\n'):
        output += '\n'
    for key, line in entries.items():
        output += f'{line.rstrip()} {MARKER}{key}\n'
    return output


def read_crontab() -> str:
    result = subprocess.run(['crontab', '-l'], capture_output=True, text=True, timeout=15)
    if result.returncode == 0:
        return result.stdout
    if result.returncode == 1 and re.fullmatch(r'no crontab for [^\r\n]+\n?', result.stderr):
        return ''
    raise RuntimeError('cannot read crontab; refusing to replace unknown contents')


def install(entries: dict[str, str], state: Path, *, verify: bool = False, retire: tuple[str, ...] = ()) -> bool:
    if verify:
        current = read_crontab()
        return reconcile(current, entries, retire) == current
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.is_symlink() or state.stat().st_mode & 0o077:
        raise RuntimeError('cron recovery directory must be private')
    fd = os.open(state / 'install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        before = read_crontab()
        after = reconcile(before, entries, retire)
        if after == before:
            return True
        backup = state / (hashlib.sha256(before.encode()).hexdigest() + '.crontab')
        if not backup.exists():
            fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, 'w') as stream:
                stream.write(before); stream.flush(); os.fsync(stream.fileno())
        if backup.is_symlink() or backup.read_text() != before or backup.stat().st_mode & 0o077:
            raise RuntimeError('cron recovery backup does not match')
        directory_fd = os.open(state, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        if read_crontab() != before:
            raise RuntimeError('crontab changed while preparing installation; retry')
        subprocess.run(['crontab', '-'], input=after, text=True, check=True, timeout=15)
        if read_crontab() != after:
            raise RuntimeError('installed crontab does not match the verified plan')
        return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--entry', nargs=2, action='append', default=[], metavar=('KEY', 'LINE'))
    parser.add_argument('--retire', action='append', default=[])
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--state', type=Path, required=True)
    args = parser.parse_args()
    if not args.entry or len(dict(args.entry)) != len(args.entry):
        parser.error('provide distinct managed entry keys')
    try:
        good = install(dict(args.entry), args.state, verify=args.verify, retire=tuple(args.retire))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f'cron installation FAILED ({type(exc).__name__}); existing entries and private recovery copies retained')
        return 1
    print('cron installation OK' if good else 'cron installation FAILED: missing, stale or duplicate managed entries')
    return 0 if good else 1


if __name__ == '__main__':
    raise SystemExit(main())
