#!/usr/bin/env python3
"""Preserve quiescent legacy module state in a separate private data root.

Quiescence is an operator prerequisite, not something a filesystem snapshot
can prove. Stop every legacy writer before invoking this tool. Interrupted
migrations retain originals or verified private backups and remain unavailable
to the MCP runtime until the completion receipt is durable.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from file_utils import atomic_write_json, fsync_directory
from spaces import discover_spaces

COMPONENTS = ('data', 'state', 'settings.local.yaml')
MAX_FILES = 10000
MAX_BYTES = 16 * 1024**3
MAX_RECEIPT_BYTES = 4 * 1024**2


def _exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _private(path, boundary):
    relative = path.relative_to(boundary)
    if any(part in ('.', '..') for part in relative.parts):
        raise ValueError('invalid private path')
    current = boundary
    private_area = False
    for component in relative.parts:
        current /= component
        try:
            current.mkdir(mode=0o700)
            fsync_directory(current.parent)
        except FileExistsError:
            pass
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('private directory is aliased or invalid')
        private_area |= component in ('module-data', 'module-data-backups')
        if private_area and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('private directory ownership or mode mismatch')
    if path.stat().st_mode & 0o077 or path.stat().st_uid != os.geteuid():
        raise ValueError('private directory ownership or mode mismatch')
    return path


@contextmanager
def _lock(parent, name):
    key = hashlib.sha256(name.encode()).hexdigest()
    fd = os.open(parent / ('.migration-' + key + '.lock'),
                 os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('migration lock is invalid')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def _file(path, output=None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
            raise ValueError('legacy state contains an unsafe file')
        digest, count = hashlib.sha256(), 0
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            count += len(block)
            if count > MAX_BYTES:
                raise ValueError('legacy state exceeds migration limit')
            digest.update(block)
            if output is not None:
                view = memoryview(block)
                while view:
                    written = os.write(output, view)
                    if written <= 0:
                        raise OSError('copy made no progress')
                    view = view[written:]
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or count != before.st_size:
            raise ValueError('legacy state changed while reading')
        return {'kind': 'file', 'bytes': count, 'sha256': digest.hexdigest()}
    finally:
        os.close(fd)


def _snapshot(root, components):
    if (not isinstance(components, list) or not components or len(components) != len(set(components))
            or any(c not in COMPONENTS for c in components)):
        raise ValueError('invalid migration components')
    result = {}
    pending = [root / name for name in components]
    total = 0
    while pending:
        path = pending.pop()
        relative = path.relative_to(root)
        if len(relative.parts) > 64 or len(result) >= MAX_FILES or path.resolve() != path.absolute():
            raise ValueError('legacy state traversal is unsafe or exceeds limits')
        info = path.lstat()
        if stat.S_ISDIR(info.st_mode):
            result[relative.as_posix()] = {'kind': 'directory'}
            with os.scandir(path) as entries:
                for entry in entries:
                    pending.append(path / entry.name)
                    if len(pending) + len(result) > MAX_FILES:
                        raise ValueError('legacy state traversal exceeds limits')
        else:
            result[relative.as_posix()] = _file(path)
            total += result[relative.as_posix()]['bytes']
            if total > MAX_BYTES:
                raise ValueError('legacy state exceeds migration limit')
    return result


def _copy(source, target, snapshot):
    for name, entry in sorted(snapshot.items(), key=lambda item: (len(Path(item[0]).parts), item[0])):
        output = target / name
        if entry['kind'] == 'directory':
            output.mkdir(mode=0o700)
        else:
            fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            try:
                if _file(source / name, fd) != entry:
                    raise ValueError('legacy state changed while copying')
                os.fsync(fd)
            finally:
                os.close(fd)
    for directory in sorted([target, *(target / n for n, v in snapshot.items() if v['kind'] == 'directory')], reverse=True):
        fsync_directory(directory)


def migrate(root, space_name, module, source, *, quiesced=False):
    if not quiesced:
        raise ValueError('legacy writers must be quiesced before migration')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?', module):
        raise ValueError('invalid module name')
    root, source = Path(root), Path(source)
    if not root.is_absolute() or not source.is_absolute():
        raise ValueError('explicit absolute roots required')
    root, source = root.resolve(strict=True), source.resolve(strict=True)
    matches = [s for s in discover_spaces(root, reject_invalid=True, reject_aliases=True) if s.name == space_name]
    if len(matches) != 1:
        raise ValueError('ambiguous target space')
    space = matches[0].path
    parent = _private(space / '.datacore/module-data', space)
    target = parent / module
    _private(target.parent, space)
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise ValueError('code and private state roots overlap')
    with _lock(parent, module):
        receipt_path = target / '.migration.json'
        if _exists(target):
            _private(target, space)
            if not _exists(receipt_path) or receipt_path.is_symlink() or receipt_path.stat().st_size > MAX_RECEIPT_BYTES:
                raise ValueError('existing private destination requires reconciliation')
            fd = os.open(receipt_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_RECEIPT_BYTES:
                    raise ValueError('invalid migration receipt')
                raw = bytearray()
                while True:
                    chunk = os.read(fd, min(65536, MAX_RECEIPT_BYTES + 1 - len(raw)))
                    if not chunk:
                        break
                    raw.extend(chunk)
                    if len(raw) > MAX_RECEIPT_BYTES:
                        raise ValueError('migration receipt exceeds limit')
                after = os.fstat(fd)
                if (len(raw) != info.st_size or info.st_mtime_ns != after.st_mtime_ns
                        or info.st_ctime_ns != after.st_ctime_ns):
                    raise ValueError('migration receipt changed during read')
            finally:
                os.close(fd)
            receipt = json.loads(raw)
            if (not isinstance(receipt, dict) or set(receipt) != {
                    'version', 'status', 'source', 'space', 'module', 'components', 'snapshot', 'backup'}
                    or type(receipt.get('version')) is not int or receipt['version'] != 1 or receipt.get('source') != str(source)
                    or receipt.get('module') != module or receipt.get('space') != space_name):
                raise ValueError('migration receipt differs from the request')
            components, snapshot = receipt['components'], receipt['snapshot']
            backup = Path(receipt['backup'])
            expected_parent = space / '.datacore/module-data-backups'
            if (not backup.is_relative_to(expected_parent)
                    or not re.fullmatch(r'[0-9a-f]{32}', backup.relative_to(expected_parent).as_posix())):
                raise ValueError('migration backup is outside private storage')
            _private(backup, space)
            if receipt.get('status') == 'complete':
                if any(_exists(source / c) for c in components) or _snapshot(backup, components) != snapshot:
                    raise ValueError('completed migration has divergent legacy state')
                # Active new data may legitimately have changed since cutover.
                return {'status': 'complete', 'retry': True, 'components': len(components)}
            if receipt.get('status') != 'staged' or _snapshot(target, components) != snapshot:
                raise ValueError('staged private destination changed')
        else:
            components = [c for c in COMPONENTS if _exists(source / c)]
            if not components:
                raise ValueError('no legacy components found')
            snapshot = _snapshot(source, components)
            backup = _private(space / '.datacore/module-data-backups' / uuid.uuid4().hex, space)
            receipt = {'version': 1, 'status': 'staged', 'source': str(source),
                       'space': space_name, 'module': module, 'components': components,
                       'snapshot': snapshot, 'backup': str(backup)}
            # Reserve the two extra bytes for staged -> complete. A migration
            # must never retire originals with a receipt its retry cannot read.
            if len((json.dumps(receipt, indent=2) + '\n').encode('utf-8')) + 2 > MAX_RECEIPT_BYTES:
                raise ValueError('migration receipt exceeds limit')
            temporary = Path(tempfile.mkdtemp(prefix='.migration-pending-', dir=target.parent))
            try:
                _copy(source, temporary, snapshot)
                if _snapshot(source, components) != snapshot or _snapshot(temporary, components) != snapshot:
                    raise ValueError('legacy state changed during preparation')
                atomic_write_json(temporary / '.migration.json', receipt)
                if _exists(target):
                    raise ValueError('private destination appeared during preparation')
                os.rename(temporary, target)
                fsync_directory(target.parent)
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        # A retry after a directory-flush failure must make publication durable
        # before retiring any original; merely seeing the copy is insufficient.
        fsync_directory(target)
        fsync_directory(target.parent)
        # Only previously verified copies may be retired. Originals remain as
        # private backups; EXDEV/permission failures retain both and propagate.
        for component in components:
            original, saved = source / component, backup / component
            expected = {n: v for n, v in snapshot.items() if n == component or n.startswith(component + '/')}
            if _exists(original):
                if _exists(saved) or _snapshot(source, [component]) != expected:
                    raise ValueError('legacy source changed or backup conflicts')
                os.rename(original, saved)
                fsync_directory(source)
                fsync_directory(backup)
            if _snapshot(backup, [component]) != expected:
                raise ValueError('retired original differs from verified copy')
        if _snapshot(target, components) != snapshot:
            raise ValueError('destination changed before completion')
        receipt['status'] = 'complete'
        atomic_write_json(receipt_path, receipt)
        fsync_directory(target.parent)
        return {'status': 'complete', 'retry': False, 'components': len(components)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('root', 'space', 'module', 'source'):
        parser.add_argument('--' + arg, required=True)
    parser.add_argument('--quiesced', action='store_true')
    args = parser.parse_args()
    try:
        result = migrate(args.root, args.space, args.module, args.source, quiesced=args.quiesced)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'status': 'incomplete', 'error': 'migration-requires-reconciliation'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
