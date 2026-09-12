"""Verify an installed public Hermes profile against its authenticated lock.

Run with the installed environment's Python, in an empty working directory.
Private plugin overlays require their own declaration and verification; they
must not silently become undeclared packages in this public base environment.
"""
from __future__ import annotations

import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import stat
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name


PROFILES = ('datacore-telegram', 'datacore-telegram-tts')


def _read(path: Path, maximum: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
            raise ValueError('invalid verification input')
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('verification input exceeds limit')
    return data


def expected_packages(profile: str, *, kit: Path | None = None) -> dict[str, str]:
    if profile not in PROFILES:
        raise ValueError('unsupported runtime profile')
    kit = Path(__file__).parent if kit is None else Path(kit)
    manifest = json.loads(_read(kit / 'manifest.json', 65536))
    if (not isinstance(manifest, dict) or type(manifest.get('format_version')) is not int
            or manifest['format_version'] != 1):
        raise ValueError('unsupported manifest format')
    raw = _read(kit / (profile + '.requirements.txt'), 2_000_000)
    if hashlib.sha256(raw).hexdigest() != manifest['requirements_sha256'][profile]:
        raise ValueError('requirements checksum mismatch')
    expected = {'hermes-agent': manifest['version']}
    for line in raw.decode('utf-8').replace('\\\n', ' ').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        line, hashes = re.subn(r'\s+--hash=sha256:[0-9a-f]{64}(?=\s|$)', '', line)
        if not hashes:
            raise ValueError('requirement has no artifact hash')
        requirement = Requirement(line)
        specs = list(requirement.specifier)
        if (requirement.url or len(specs) != 1 or specs[0].operator != '=='
                or '*' in specs[0].version):
            raise ValueError('requirement is not exactly pinned')
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        name = canonicalize_name(requirement.name)
        if name in expected:
            raise ValueError('duplicate selected package')
        expected[name] = specs[0].version
    return expected


def compare_packages(expected: dict[str, str], installed) -> dict:
    actual = {}
    duplicates = set()
    for name, version in installed:
        name = canonicalize_name(name)
        if name in actual:
            duplicates.add(name)
        actual[name] = version
    missing = sorted(expected.keys() - actual.keys())
    extra = sorted(actual.keys() - expected.keys())
    mismatched = sorted(name for name in expected.keys() & actual.keys()
                        if expected[name] != actual[name])
    return {
        'status': 'DRIFT' if missing or extra or mismatched or duplicates else 'PASS',
        'packages': len(actual), 'missing': missing, 'extra': extra,
        'version_mismatch': mismatched, 'duplicate_distributions': sorted(duplicates),
    }


def main(argv=None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1 or args[0] not in PROFILES:
        print('usage: verify_environment.py PROFILE', file=sys.stderr)
        return 2
    try:
        expected = expected_packages(args[0])
        result = compare_packages(expected, ((d.metadata['Name'], d.version)
                                             for d in metadata.distributions()))
        result.update({'profile': args[0], 'python': sys.version.split()[0]})
        print(json.dumps(result, sort_keys=True))
        return 0 if result['status'] == 'PASS' else 1
    except (OSError, ValueError, KeyError, TypeError):
        print('runtime verification refused: invalid manifest, lock or package metadata', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
