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
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))

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


def _exists(path):
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _contains(parent, child):
    return child == parent or parent in child.parents


def _directory_evidence(path, boundary, *, permissions=False):
    """Inspect without creating or following any directory aliases."""
    current = boundary
    private = False
    for component in path.relative_to(boundary).parts:
        current /= component
        try:
            info = current.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('module data directory is aliased or invalid')
        private |= component in ('module-data', 'module-data-backups')
        if permissions and (private or current == path and path.name == 'data') and (info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise ValueError('private directory ownership or mode mismatch')


def data_path(space_root, module, code, space_name, *, create=True):
    """Retain an existing data layout; a directory rename is not a safety rule."""
    space_root = Path(space_root).resolve(strict=True)
    code = Path(code).resolve()
    legacy = space_root / '.datacore/modules' / module
    separate = space_root / '.datacore/module-data' / module
    components = ('data', 'state', 'settings.local.yaml')

    # Actual private state inside installed code still requires explicit repair.
    # Existing private state in a separate user-space directory does not.
    if any(_exists(code / component) for component in components):
        raise ValueError('private state in installed code requires preserved migration')
    legacy_is_code = (_contains(code, legacy.resolve()) or any(
        _exists(legacy / marker) for marker in ('module.yaml', '.git', 'tools')))
    legacy_state = any(_exists(legacy / component) for component in components)
    if legacy_is_code and legacy_state:
        raise ValueError('private state in installed code requires preserved migration')
    if not legacy_is_code:
        _directory_evidence(legacy, space_root)
    _directory_evidence(separate, space_root)
    separate_state = any(_exists(separate / component) for component in
                         (*components, '.migration.json'))
    if legacy_state and separate_state:
        raise ValueError('multiple module data stores require explicit reconciliation')
    target = separate if legacy_is_code or separate_state else legacy
    if _contains(code, target.resolve()):
        raise ValueError('module data cannot be stored inside installed code')
    _directory_evidence(target / 'data', space_root, permissions=True)
    receipt = read_text(space_root, target / '.migration.json', limit=4 * 1024**2)
    if receipt is not None:
        record = parse_json(receipt)
        if (not isinstance(record, dict) or type(record.get('version')) is not int
                or record['version'] != 1 or record.get('status') != 'complete'
                or record.get('module') != module or record.get('space') != space_name):
            raise ValueError('module migration is incomplete or bound to another space')
    if create:
        _private(target / 'data', space_root)
    return target / 'data'


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
    return ModuleContext(root, space_root, entry['name'],
                         data_path(space_root, module, code, entry['name'], create=create))


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--space-root', required=True)
    parser.add_argument('--space-name', required=True)
    parser.add_argument('--module', required=True)
    parser.add_argument('--code', required=True)
    args = parser.parse_args()
    try:
        if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)?', args.module):
            raise ValueError('invalid module')
        result = data_path(args.space_root, args.module, args.code, args.space_name)
        print(json.dumps({'version': 1, 'data_path': str(result)}))
    except (ValueError, OSError):
        parser.exit(2, 'Module data resolution failed; preserve existing state and reconcile the layout.\n')


if __name__ == '__main__':
    main()
