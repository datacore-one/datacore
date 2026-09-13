#!/usr/bin/env python3
"""Start an installed stdio provider with its explicitly assigned environment.

Profiles are trusted administrator/operator configuration. This launcher removes
ambient credential inheritance; independent process/data isolation still requires
the runtime service boundary. It never installs packages or loads shared .env.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime_context import Refused, _environment, _pairs  # noqa: E402

MAX_BYTES = 65536


def credential_digest(credentials: dict) -> str:
    """Bind a profile to complete credential contents, independent of spacing."""
    raw = json.dumps(credentials, sort_keys=True, ensure_ascii=True, allow_nan=False,
                     separators=(',', ':')).encode('ascii')
    return hashlib.sha256(raw).hexdigest()


def _path(value: object) -> Path:
    if (not isinstance(value, str) or not value or '\0' in value
            or not Path(value).is_absolute() or '..' in Path(value).parts):
        raise Refused('an explicit absolute path is required')
    return Path(value)


def _private_directory(path: Path) -> None:
    # Symlinked credentials/configuration roots are ambiguous during cutover.
    for part in [*reversed(path.parents), path]:
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise Refused('directory aliases are not supported')
    info = path.stat()
    if info.st_uid not in {0, os.geteuid()} or info.st_mode & 0o077:
        raise Refused('provider directory must be private')


def _read_private(path: Path) -> dict:
    _private_directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid not in {0, os.geteuid()} or before.st_mode & 0o077
                or before.st_size > MAX_BYTES):
            raise Refused('provider configuration must be a bounded private file')
        raw = bytearray()
        while len(raw) <= MAX_BYTES:
            chunk = os.read(fd, min(8192, MAX_BYTES + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if (len(raw) > MAX_BYTES or len(raw) != before.st_size
                or identity(before) != identity(after)):
            raise Refused('provider configuration changed during read')
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
        if not isinstance(value, dict):
            raise Refused('provider configuration must be a mapping')
        return value
    finally:
        os.close(fd)


def build_launch(profile: dict, secrets: dict) -> tuple[list[str], dict[str, str], Path]:
    required = {'version', 'command', 'home', 'cwd', 'environment', 'credential_names', 'credential_sha256'}
    if (set(profile) != required or type(profile['version']) is not int
            or profile['version'] != 1):
        raise Refused('unsupported provider profile')
    command = profile['command']
    if (not isinstance(command, list) or not 1 <= len(command) <= 64
            or any(not isinstance(arg, str) or '\0' in arg for arg in command)):
        raise Refused('invalid provider command')
    selected_executable = _path(command[0])
    executable = selected_executable.resolve(strict=True)
    info = executable.stat()
    if (not stat.S_ISREG(info.st_mode) or not os.access(executable, os.X_OK)
            or info.st_mode & 0o022 or info.st_uid not in {0, os.geteuid()}):
        raise Refused('a qualified installed executable is required')
    # A profile names an installed server, not a request-time package resolver
    # or shell expression. This is configuration validation, not a sandbox for
    # hostile profiles or a guarantee about what the provider itself executes.
    unsupported = {'npx', 'npm', 'npx-cli.js', 'npm-cli.js', 'pnpm', 'yarn', 'bunx',
                   'bash', 'sh', 'zsh', 'dash', 'env'}
    if {selected_executable.name, executable.name} & unsupported:
        raise Refused('package acquisition and shell profiles are unsupported')
    if executable.name.startswith('python') and (len(command) < 2 or command[1] != '-I'):
        raise Refused('Python providers require isolated startup')
    home, cwd = _path(profile['home']), _path(profile['cwd'])
    _private_directory(home)
    _private_directory(cwd)
    environment = _environment(profile['environment'])
    credentials = _environment(secrets)
    names = profile['credential_names']
    if (not isinstance(names, list) or any(not isinstance(key, str) for key in names)
            or len(names) != len(set(names)) or set(names) != set(credentials)
            or set(environment) & set(credentials)):
        raise Refused('credentials differ from the declared provider scope')
    expected_digest = profile['credential_sha256']
    if (not isinstance(expected_digest, str) or len(expected_digest) != 64
            or any(c not in '0123456789abcdef' for c in expected_digest)
            or not hmac.compare_digest(expected_digest, credential_digest(credentials))):
        raise Refused('profile and credential versions do not match')
    clean = {'HOME': str(home), 'PATH': str(selected_executable.parent) + ':/usr/bin:/bin',
             'LANG': 'C.UTF-8', 'PYTHONNOUSERSITE': '1'}
    clean.update(environment)
    clean.update(credentials)
    # Inspect the resolved binary, but execute the selected entry point. A venv
    # interpreter is commonly a symlink; resolving it for exec loses the venv.
    return command.copy(), clean, cwd


def launch(profile_path: Path, credentials_path: Path) -> None:
    command, environment, cwd = build_launch(_read_private(profile_path), _read_private(credentials_path))
    os.chdir(cwd)
    os.umask(0o077)
    # Only stdin/stdout/stderr belong to stdio. An inherited credential/socket
    # descriptor must not provide a second route around environment scoping.
    # A parent can lower RLIMIT_NOFILE after opening a high descriptor. The
    # current limit therefore does not bound inherited descriptor numbers.
    # Both supported POSIX deployments (Linux/macOS) expose actual fds here.
    descriptors = [int(name) for name in os.listdir('/dev/fd') if name.isdecimal()]
    for fd in descriptors:
        if fd > 2:
            os.closerange(fd, fd + 1)  # the listing's own fd may already be closed
    os.execve(command[0], command, environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', required=True)
    parser.add_argument('--credentials', required=True)
    args = parser.parse_args(argv)
    try:
        launch(_path(args.profile), _path(args.credentials))
    except (OSError, ValueError, TypeError, KeyError):
        # JSON/exec errors can embed credential bytes. Never forward them.
        print('MCP startup refused: installed profile or credential validation failed', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
