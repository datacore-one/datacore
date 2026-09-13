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

from file_utils import read_text_within as read_text
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
