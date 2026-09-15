#!/usr/bin/env python3
"""Prepare the pinned Hermes source without network access or build hooks."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

KIT = Path(__file__).resolve().parent
sys.path.insert(0, str(KIT.parent))
from safe_move import rename_directory_noreplace  # noqa: E402



def _read_regular(path: Path, limit: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('input must be a bounded regular file')
        data = source.read(limit + 1)
        if len(data) > limit:
            raise ValueError('input exceeded its size limit')
        return data


def _checked(path: Path, expected: str, limit: int) -> bytes:
    data = _read_regular(path, limit)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('input checksum mismatch')
    return data


def _extract(data: bytes, root_name: str, destination: Path) -> None:
    """Reject links, duplicate entries and escapes before writing any member."""
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as archive:
        members = archive.getmembers()
        if len(members) > 10_000 or sum(m.size for m in members) > 200_000_000:
            raise ValueError('source archive exceeds its size limits')
        seen = set()
        validated = []
        for member in members:
            path = PurePosixPath(member.name)
            if (path.is_absolute() or '..' in path.parts or not path.parts
                    or path.parts[0] != root_name or '\\' in member.name
                    or not (member.isfile() or member.isdir()) or path in seen):
                raise ValueError('unsafe or duplicate source archive member')
            seen.add(path)
            relative = Path(*path.parts[1:])
            if relative == Path('.') and not member.isdir():
                raise ValueError('source archive root must be a directory')
            validated.append((member, relative))
        for member, relative in validated:
            target = destination / relative
            if member.isdir():
                target.mkdir(mode=0o755, parents=True, exist_ok=True)
            else:
                target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError('source archive member has no data')
                with source, target.open('xb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(0o644)


def prepare(archive: Path, destination: Path, *, kit: Path = KIT) -> Path:
    """Publish a new source tree only after all pinned inputs verify."""
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists')
    manifest = json.loads(_read_regular(kit / 'manifest.json', 1_000_000))
    if manifest['format_version'] != 1:
        raise ValueError('unsupported manifest version')
    source = _checked(archive, manifest['source_sha256'], 30_000_000)
    patch = _checked(kit / 'source.patch', manifest['patch_sha256'], 1_000_000)
    lock = _checked(kit / 'uv.lock', manifest['lock_sha256'], 2_000_000)
    for name, digest in manifest['requirements_sha256'].items():
        if Path(name).name != name:
            raise ValueError('invalid requirements profile')
        _checked(kit / (name + '.requirements.txt'), digest, 2_000_000)
    # No source build hooks or package installation occurs in this process.
    with tempfile.TemporaryDirectory(prefix='.hermes-prepare-', dir=destination.parent.resolve()) as temporary:
        work = Path(temporary)
        tree = work / 'source'
        tree.mkdir(mode=0o755)
        _extract(source, manifest['archive_root'], tree)
        patch_path = work / 'source.patch'
        patch_path.write_bytes(patch)
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C',
               'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_CONFIG_NOSYSTEM': '1'}
        command = ['/usr/bin/git', 'apply', '--whitespace=error', str(patch_path)]
        for args in [command[:2] + ['--check'] + command[2:], command]:
            result = subprocess.run(args, cwd=tree, env=env, capture_output=True,
                                    timeout=30, check=False)
            if result.returncode:
                raise ValueError('backport patch did not apply cleanly')
        (tree / 'uv.lock').write_bytes(lock)
        for name, digest in manifest['patched_files_sha256'].items():
            relative = PurePosixPath(name)
            if relative.is_absolute() or '..' in relative.parts:
                raise ValueError('invalid patched file path')
            _checked(tree / name, digest, 2_000_000)
        # The OS refuses replacement, including a directory created after our
        # initial check. A concurrent preparation cannot erase another result.
        rename_directory_noreplace(tree, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    try:
        prepare(args.archive, args.destination)
    except (OSError, ValueError, KeyError, tarfile.TarError, subprocess.SubprocessError):
        print('Hermes source preparation refused; no verified source tree was published.')
        return 1
    print('Pinned Hermes source prepared; build and runtime deployment are separate steps.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
