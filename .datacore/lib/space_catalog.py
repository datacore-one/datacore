#!/usr/bin/env python3
"""Versioned, content-free space discovery for non-Python runtime clients.

The implementation of discovery lives exclusively in spaces.py (DIP-0015).
Clients must refuse incomplete/invalid catalogs rather than infer destinations.
This catalog describes accessible spaces; it is not an authorization boundary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from spaces import discover_spaces


def catalog(root):
    root = Path(root)
    if not root.is_absolute() or not root.is_dir():
        raise ValueError('explicit existing installation required')
    root = root.resolve(strict=True)
    result = []
    names = set()
    for space in discover_spaces(root, reject_aliases=True, reject_invalid=True):
        if space.name in names:
            raise ValueError('ambiguous space identity')
        names.add(space.name)
        relative = space.path.relative_to(root).as_posix()
        result.append({'path': relative, 'name': space.name,
                       'type': space.type, 'marked': space.marked})
    return {'version': 1, 'spaces': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    args = parser.parse_args()
    try:
        result = catalog(args.root)
    except (OSError, ValueError):
        # No paths, parser excerpts, owners, or configuration values in errors.
        print(json.dumps({'version': 1, 'error': 'discovery-unverified'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
