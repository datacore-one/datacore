#!/usr/bin/env python3
"""Migrate heartbeat observations through the installed Ventures state writer.

The canonical writer preserves legacy observations before deriving a view.
Invalid evidence holds migration; a dry run validates without publishing.
Never delete source shards as a rollback: retain them for reconciliation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys

LIB = Path(__file__).resolve().parent
ROOT = LIB.parents[1]


def _state_writer():
    """Load matching installed code, independently of the selected data root."""
    library = LIB.parent / 'modules/ventures/lib'
    if library.is_symlink() or not (library / 'heartbeat_state.py').is_file():
        raise RuntimeError('a compatible installed Ventures state writer is required')
    from file_utils import read_text_within
    for filename in ('__init__.py', 'heartbeat_state.py'):
        if read_text_within(LIB.parent, library / filename) is None:
            raise RuntimeError('installed heartbeat writer is incomplete')
    library = library.resolve(strict=True)
    name = '_datacore_heartbeat_migration_' + hashlib.sha256(str(library).encode()).hexdigest()
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, library / '__init__.py',
                                                      submodule_search_locations=[str(library)])
        package = importlib.util.module_from_spec(spec)
        sys.modules[name] = package
        spec.loader.exec_module(package)
    return importlib.import_module(name + '.heartbeat_state')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=ROOT)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    try:
        from intent_sources import spaces
        root = args.data_dir.resolve(strict=True)
        entries = spaces(root)
        writer = _state_writer()
    except (OSError, ValueError, ImportError, RuntimeError) as exc:
        print(f'heartbeat migration unavailable: {type(exc).__name__}', file=sys.stderr)
        return 1
    migrated = unchanged = failed = 0
    for entry in entries:
        try:
            result = writer.migrate_heartbeat(root / entry['path'], dry_run=not args.apply)
            if result is None:
                unchanged += 1
            else:
                migrated += 1
        except (OSError, ValueError, RuntimeError) as exc:
            failed += 1
            print(f'heartbeat migration held: {type(exc).__name__}', file=sys.stderr)
    print(f'{migrated} migration(s), {unchanged} unchanged, {failed} held'
          + (' [dry run]' if not args.apply else ''))
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
