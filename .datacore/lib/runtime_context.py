#!/usr/bin/env python3
"""Launch a worker inside datacore-runtime@.service's independent OS boundary.

The system unit supplies isolation; this launcher refuses accidental direct or
misconfigured invocation and constructs an explicit process environment. It
does not provision accounts, copy live databases, or grant host data access.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import stat
import sys


CONFIG = Path('/etc/datacore/runtime')
STATE = Path('/var/lib/datacore-workers')
CREDENTIALS = Path('/run/credentials')
MAX_CONFIG_BYTES = 65536
NAME = re.compile(r'[a-z][a-z0-9-]{0,23}\Z')
ENV_NAME = re.compile(r'[A-Z][A-Z0-9_]{0,79}\Z')
RESERVED = {'HOME', 'USER', 'LOGNAME', 'SHELL', 'PATH', 'PWD',
            'DATACORE_ROOT', 'DATACORE_STATE', 'CREDENTIALS_DIRECTORY',
            'ENV', 'BASH_ENV', 'SSH_AUTH_SOCK', 'SSH_AGENT_PID', 'NODE_OPTIONS'}


class Refused(ValueError):
    """A runtime boundary or its configuration could not be established."""


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('duplicate configuration key')
        result[key] = value
    return result


def _json_file(path: Path, *, root_owned: bool) -> dict:
    if root_owned:
        for parent in [*reversed(path.parents), path]:
            info = parent.lstat()
            if (stat.S_ISLNK(info.st_mode) or info.st_uid != 0
                    or info.st_mode & 0o022):
                raise Refused('configuration is not administrator controlled')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_BYTES:
            raise Refused('invalid configuration file')
        if root_owned and (info.st_uid != 0 or info.st_mode & 0o022):
            raise Refused('configuration is not administrator controlled')
        with os.fdopen(fd, 'r', encoding='utf-8') as stream:
            fd = -1
            raw = stream.read(MAX_CONFIG_BYTES + 1)
        if len(raw.encode('utf-8')) > MAX_CONFIG_BYTES:
            raise Refused('configuration exceeds size limit')
        value = json.loads(raw, object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise Refused('configuration must be a mapping')
        return value
    finally:
        if fd != -1:
            os.close(fd)


def _environment(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise Refused('environment must be a mapping')
    for key, text in value.items():
        if (not isinstance(key, str) or not ENV_NAME.fullmatch(key)
                or key in RESERVED or key.startswith(('LD_', 'DYLD_', 'PYTHON'))
                or not isinstance(text, str) or '\0' in text):
            raise Refused('invalid or reserved environment entry')
    return dict(value)


def build_launch(name: str, profile: dict, secrets: dict) -> tuple[list[str], dict[str, str]]:
    """Validate an administrator profile; never merge the ambient environment."""
    if not isinstance(name, str) or not NAME.fullmatch(name):
        raise Refused('invalid runtime name')
    expected = {'version', 'command', 'environment', 'credential_names'}
    if set(profile) != expected or type(profile['version']) is not int or profile['version'] != 1:
        raise Refused('unsupported runtime profile')
    command = profile['command']
    if (not isinstance(command, list) or not command or len(command) > 64
            or any(not isinstance(arg, str) or '\0' in arg for arg in command)
            or not command[0].startswith(('/opt/datacore/', '/usr/'))
            or '..' in Path(command[0]).parts):
        raise Refused('invalid runtime command')
    environment = _environment(profile['environment'])
    credentials = _environment(secrets)
    names = profile['credential_names']
    if (not isinstance(names, list) or any(not isinstance(key, str) for key in names)
            or len(names) != len(set(names)) or set(names) != set(credentials)
            or set(environment) & set(credentials)):
        raise Refused('credential scope differs from the runtime profile')
    home = str(STATE / name)
    clean = {
        'HOME': home, 'PATH': str(Path(command[0]).parent) + ':/usr/bin:/bin',
        'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1',
        'DATACORE_ROOT': home + '/Data', 'DATACORE_STATE': home + '/state',
    }
    clean.update(environment)
    clean.update(credentials)
    return command.copy(), clean


def check_boundary(name: str) -> None:
    uid = os.getuid()
    if uid != os.geteuid() or not 61184 <= uid <= 65519:
        raise Refused('an independent systemd dynamic identity is required')
    # DynamicUser can reuse a same-named static account. Never accept a local
    # administrator-created account in place of the allocated transient UID.
    for line in Path('/etc/passwd').read_text().splitlines():
        fields = line.split(':')
        if len(fields) >= 3 and fields[2] == str(uid):
            raise Refused('a static account cannot run this worker unit')
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
    if (status.get('NoNewPrivs', '').strip() != '1'
            or any(int(status.get(key, '1').strip(), 16) for key in ('CapEff', 'CapPrm', 'CapBnd', 'CapAmb'))):
        raise Refused('worker privilege restrictions are absent')
    state = STATE / name
    info = state.stat()
    if not state.is_dir() or info.st_uid != uid or info.st_mode & 0o077:
        raise Refused('worker state is not private to its runtime identity')
    if Path.cwd().resolve() != state.resolve():
        raise Refused('worker working directory does not match its state')


def launch(name: str) -> None:
    if not NAME.fullmatch(name):
        raise Refused('invalid runtime name')
    check_boundary(name)
    profile = _json_file(CONFIG / f'{name}.json', root_owned=True)
    # systemd mounts these credentials read-only inside the unit's namespace.
    # The location is derived from the unit name, never an inherited variable.
    secrets = _json_file(CREDENTIALS / f'datacore-runtime@{name}.service/secrets.json', root_owned=False)
    command, environment = build_launch(name, profile, secrets)
    os.umask(0o077)
    os.execve(command[0], command, environment)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] != 'launch':
        print('usage: runtime_context.py launch CONTEXT', file=sys.stderr)
        return 2
    try:
        launch(args[1])
    except (Refused, OSError, ValueError, KeyError, TypeError):
        # Parser and exec exceptions may contain secrets or configuration text.
        print('runtime refused: boundary or configuration validation failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
