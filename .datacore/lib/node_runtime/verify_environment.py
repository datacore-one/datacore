#!/usr/bin/env python3
"""Verify the declared Node/PLUR profile without importing installed packages.

This verifies declared inputs and installed package identity, not administrator
ownership or runtime behavior. Seal the release and run verify-plur.mjs too.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat

HERE = Path(__file__).resolve().parent


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate metadata key')
        result[key] = value
    return result


def _read(path: Path, limit: int = 2_000_000) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('invalid metadata file')
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('metadata limit exceeded')
        return data
    finally:
        if fd != -1:
            os.close(fd)


def _binary_digest(path: Path) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        limit = 512 * 1024 * 1024
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('invalid Node executable')
        digest = hashlib.sha256()
        count = 0
        with os.fdopen(fd, 'rb') as stream:
            fd = -1
            while chunk := stream.read(1024 * 1024):
                count += len(chunk)
                if count > limit:
                    raise ValueError('Node executable exceeds limit')
                digest.update(chunk)
        return digest.hexdigest()
    finally:
        if fd != -1:
            os.close(fd)


def _json(path: Path) -> dict:
    value = json.loads(_read(path), object_pairs_hook=_pairs)
    if not isinstance(value, dict):
        raise ValueError('metadata must be a mapping')
    return value


def _package_path(root: Path, relative: str) -> Path:
    parts = PurePosixPath(relative).parts
    if (not parts or parts[0] != 'node_modules' or relative != '/'.join(parts)
            or any(p in ('.', '..') for p in parts) or '\\' in relative):
        raise ValueError('invalid locked package path')
    path = root
    for part in parts:
        path /= part
        if path.is_symlink():
            raise ValueError('installed package directory is an alias')
        if path.exists() and not path.is_dir():
            raise ValueError('installed package path is not a directory')
    return path


def verify_packages(root: Path, lock: dict, required: list[str]) -> dict:
    packages = lock.get('packages')
    if lock.get('lockfileVersion') != 3 or not isinstance(packages, dict):
        raise ValueError('unsupported lock metadata')
    installed = {}
    missing_optional = []
    for relative, entry in packages.items():
        if relative == '':
            continue
        path = _package_path(root, relative)
        if not isinstance(entry, dict) or entry.get('link'):
            raise ValueError('unsupported package entry')
        name = relative.rsplit('node_modules/', 1)[-1]
        expected_name = entry.get('name', name)
        if not path.exists():
            if entry.get('optional') is True and expected_name not in required:
                missing_optional.append(relative)
                continue
            raise ValueError('required package is missing')
        metadata = _json(path / 'package.json')
        if metadata.get('name') != expected_name or metadata.get('version') != entry.get('version'):
            raise ValueError('installed package differs from the lock')
        installed[relative] = {'name': expected_name, 'version': entry['version']}
    if not set(required) <= {p['name'] for p in installed.values()}:
        raise ValueError('required runtime feature is unavailable')
    # Follow only npm's directory structure, not package test fixtures or .bin.
    pending = [root / 'node_modules']
    observed = set()
    while pending:
        directory = pending.pop()
        if not directory.exists():
            continue
        if directory.is_symlink():
            raise ValueError('installed dependency directory is an alias')
        entries = []
        for child in directory.iterdir():
            if child.name.startswith('.'):
                continue
            if child.name.startswith('@'):
                if child.is_symlink() or not child.is_dir():
                    raise ValueError('invalid package scope directory')
                entries.extend(child.iterdir())
            else:
                entries.append(child)
        for path in entries:
            relative = path.relative_to(root).as_posix()
            _package_path(root, relative)
            if relative not in installed:
                raise ValueError('unlocked installed package')
            observed.add(relative)
            pending.append(path / 'node_modules')
    if observed != set(installed):
        raise ValueError('package inventory is incomplete')
    return {'packages': len(installed), 'missing_optional': len(missing_optional)}


def verify(root: Path, node: Path, kit: Path = HERE) -> dict:
    profile = _json(kit / 'manifest.json')
    if profile.get('version') != 1:
        raise ValueError('unsupported profile')
    root = root.resolve(strict=True)
    for installed, source in [('package.json', 'plur.package.json'),
                              ('package-lock.json', 'plur.package-lock.json'),
                              ('verify-plur.mjs', 'verify-plur.mjs'),
                              ('deny-archive.cjs', 'deny-archive.cjs')]:
        reference = _read(kit / source)
        digest = hashlib.sha256(reference).hexdigest()
        if digest != profile['files'][source] or _read(root / installed) != reference:
            raise ValueError('deployment inputs differ from the declared profile')
    result = verify_packages(root, _json(root / 'package-lock.json'), profile['required_packages'])
    # Hash the binary, rather than trusting an arbitrary command's --version.
    digest = _binary_digest(node)
    if digest != profile['node']['linux_x64_binary_sha256']:
        raise ValueError('Node binary differs from the qualified release')
    return {'status': 'PASS', 'node': profile['node']['version'], **result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('installation', type=Path, help='directory containing the profile package.json')
    parser.add_argument('--node', type=Path, required=True, help='qualified Node executable')
    args = parser.parse_args()
    try:
        result = verify(args.installation, args.node)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({'status': 'REFUSED', 'reason': 'runtime profile or installed inventory mismatch'}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
