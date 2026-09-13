"""Canonical private module context for installed Python clients (DIP-0022).

Routing is not a sandbox. The deployment must restrict the process identity's
accessible data and credentials independently.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat

from module_data_migrate import _private
from space_catalog import catalog


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate JSON key')
        value[key] = item
    return value


def parse_json(raw):
    def invalid(_):
        raise ValueError('nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=_unique, parse_constant=invalid)


def read_text(root, path, *, limit=16 * 1024**2):
    """Read a bounded regular single-link file within an explicit real root."""
    root = Path(root).resolve(strict=True)
    path = Path(path)
    relative = path.relative_to(root)
    if not relative.parts or any(p in ('.', '..') for p in relative.parts):
        raise ValueError('invalid module data file')
    current = root
    for part in relative.parts[:-1]:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('module data directory is aliased or invalid')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('module data file is unsafe or exceeds limit')
        raw = bytearray()
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > limit:
                raise ValueError('module data exceeds limit')
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(raw) != before.st_size:
            raise ValueError('module data changed during read')
        return raw.decode('utf-8')
    finally:
        os.close(fd)


@dataclass(frozen=True)
class ModuleContext:
    root: Path
    space: Path
    name: str
    data: Path


def resolve(module, code, *, root=None, space=None, create=True):
    if not isinstance(module, str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?', module):
        raise ValueError('invalid module identifier')
    selected = root if root is not None else os.environ.get('DATACORE_ROOT')
    if not selected or not Path(selected).is_absolute():
        raise ValueError('an explicit absolute data root is required')
    root = Path(selected).resolve(strict=True)
    rows = catalog(root)['spaces']
    chosen = space if space is not None else os.environ.get('DATACORE_SPACE')
    if chosen is not None:
        matches = [row for row in rows if row['name'] == chosen]
    else:
        matches = [row for row in rows if row['type'] == 'personal'
                   or (not row['marked'] and row['name'] == 'personal')]
    if len(matches) != 1:
        raise ValueError('module space is missing or ambiguous')
    entry = matches[0]
    space_root = root / entry['path']
    for candidate in (space_root / '.datacore/modules' / module, Path(code)):
        for component in ('data', 'state', 'settings.local.yaml'):
            path = candidate / component
            if path.exists() or path.is_symlink():
                raise ValueError('legacy module state requires preserved migration')
    target = space_root / '.datacore/module-data' / module
    current = space_root
    private_area = False
    for part in (target / 'data').relative_to(space_root).parts:
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        private_area |= part == 'module-data'
        if not stat.S_ISDIR(info.st_mode) or (private_area and (
                info.st_uid != os.geteuid() or info.st_mode & 0o077)):
            raise ValueError('module data directory is not private or is aliased')
    receipt = read_text(space_root, target / '.migration.json', limit=4 * 1024**2)
    if receipt is not None:
        record = parse_json(receipt)
        if (not isinstance(record, dict) or type(record.get('version')) is not int
                or record['version'] != 1 or record.get('status') != 'complete'
                or record.get('module') != module or record.get('space') != entry['name']):
            raise ValueError('module migration is incomplete or bound to another space')
    if create:
        _private(target / 'data', space_root)
    return ModuleContext(root, space_root, entry['name'], target / 'data')
