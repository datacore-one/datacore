#!/usr/bin/env python3
"""Shared file utilities for Datacore — atomic writes and advisory locking.

All write operations use mkstemp + os.replace for crash safety.
Advisory locking via fcntl.flock() prevents concurrent session corruption.
"""
import fcntl
import json
import os
import re
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable



def _log(msg: str):
    print(f"[file_utils] {msg}", file=sys.stderr)


def read_text_within(root, path, *, limit=16 * 1024**2):
    """Bounded UTF-8 snapshot beneath a real root, without following aliases.

    Only absence returns None. Permission, decoding, type and concurrent-change
    errors propagate. Directory descriptors bind each lookup, so replacing an
    intermediate directory with a symlink cannot redirect a subsequent open.
    This read boundary does not isolate processes sharing an OS identity.
    """
    root = Path(root).resolve(strict=True)
    path = Path(path)
    relative = path.relative_to(root)
    if (not relative.parts or '..' in relative.parts
            or type(limit) is not int or limit < 0):
        raise ValueError('invalid bounded source read')
    descriptors = []
    chain = []
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for part in relative.parts[:-1]:
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=parent)
            except FileNotFoundError:
                return None
            descriptors.append(child)
            chain.append((parent, part, child))
            parent = child
        try:
            fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=parent)
        except FileNotFoundError:
            return None
        descriptors.append(fd)
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > limit:
            raise ValueError('source must be a bounded regular file with one link')
        raw = bytearray()
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - len(raw)))
            if not chunk:
                break
            raw.extend(chunk)
            if len(raw) > limit:
                raise ValueError('source exceeds read limit')
        def stamp(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
                    info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (stamp(before) != stamp(os.fstat(fd)) or len(raw) != before.st_size
                or stamp(before) != stamp(os.stat(relative.name, dir_fd=parent, follow_symlinks=False))):
            raise ValueError('source changed during read')
        for parent_fd, name, child_fd in chain:
            observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            held = os.fstat(child_fd)
            if (observed.st_dev, observed.st_ino, observed.st_mode) != (
                    held.st_dev, held.st_ino, held.st_mode):
                raise ValueError('source directory changed during read')
        held_root = os.fstat(descriptors[0])
        observed_root = root.lstat()
        if (held_root.st_dev, held_root.st_ino, held_root.st_mode) != (
                observed_root.st_dev, observed_root.st_ino, observed_root.st_mode):
            raise ValueError('source root changed during read')
        return raw.decode('utf-8')
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def atomic_write_text(path: Path, content: str) -> None:
    """Publish complete UTF-8 content only after flushing it to disk.

    A failure before replacement preserves the previous file. A directory
    flush failure after replacement is surfaced: the new file is visible,
    but its persistence across a system crash cannot be acknowledged.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        remaining = memoryview(content.encode("utf-8"))
        while remaining:
            written = os.write(fd, remaining)
            if written <= 0:
                raise OSError("file write made no progress")
            remaining = remaining[written:]
        os.fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(tmp_path, path)
        fsync_directory(path.parent)
    except Exception:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text_within(root, path, content, *, overwrite=True):
    """Durable publication through held directory descriptors, refusing aliases.

    Parent creation and replacement cannot be redirected by a symlink swap.
    A moved directory is reported as failure; this is not OS isolation against
    another process that can rename directories under the same identity.
    """
    import uuid
    if type(overwrite) is not bool:
        raise ValueError('overwrite policy must be boolean')
    root = Path(root).resolve(strict=True)
    relative = Path(path).relative_to(root)
    if not relative.parts or '..' in relative.parts:
        raise ValueError('invalid bounded publication')
    descriptors, chain = [], []
    temporary = None
    parent = None
    try:
        parent = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(parent)
        for part in relative.parts[:-1]:
            try:
                os.mkdir(part, mode=0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent)
            descriptors.append(child)
            chain.append((parent, part, child))
            parent = child

        def validate():
            for held_parent, name, held_child in chain:
                seen = os.stat(name, dir_fd=held_parent, follow_symlinks=False)
                held = os.fstat(held_child)
                if (seen.st_dev, seen.st_ino, seen.st_mode) != (held.st_dev, held.st_ino, held.st_mode):
                    raise ValueError('publication directory changed')
            seen, held = root.lstat(), os.fstat(descriptors[0])
            if (seen.st_dev, seen.st_ino, seen.st_mode) != (held.st_dev, held.st_ino, held.st_mode):
                raise ValueError('publication root changed')
            try:
                seen = os.stat(relative.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return
            if not stat.S_ISREG(seen.st_mode) or seen.st_nlink != 1:
                raise ValueError('publication target must be a regular file with one link')

        validate()
        temporary = '.' + uuid.uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=parent)
        try:
            remaining = memoryview(content.encode('utf-8'))
            while remaining:
                count = os.write(fd, remaining)
                if count <= 0:
                    raise OSError('file write made no progress')
                remaining = remaining[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        validate()
        if overwrite:
            os.replace(temporary, relative.name, src_dir_fd=parent, dst_dir_fd=parent)
        else:
            # Atomic no-clobber publication using the held directory. Neither
            # an existing file nor one created after validation can be replaced.
            # An interruption before unlink can leave the complete temporary
            # hard link; this is unacknowledged state requiring reconciliation.
            os.link(temporary, relative.name, src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
            os.unlink(temporary, dir_fd=parent)
        temporary = None
        os.fsync(parent)
        validate()
    finally:
        if temporary is not None and parent is not None:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
        for fd in reversed(descriptors):
            os.close(fd)


def fsync_directory(path: Path) -> None:
    """Persist directory entry changes (create/rename) on supported hosts."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_state_directory(namespace: str = '', *, data_root: Path | None = None) -> Path:
    """Create runtime state outside data/code repositories, with private modes.

    This validates the application's output boundary. It does not isolate two
    processes running as the same OS identity; use separate service identities
    for that. Existing aliases or unsafe permissions fail without chmod/moves.
    """
    selected = os.environ.get('DATACORE_STATE')
    directory = Path(selected) if selected is not None else Path.home() / '.datacore/state'
    if (selected == '' or not directory.is_absolute() or directory == Path(directory.anchor)
            or '..' in directory.parts
            or directory.resolve() != directory):
        raise ValueError('runtime state must have an absolute unaliased path')
    components = namespace.split('/') if namespace else []
    if any(not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}', part) for part in components):
        raise ValueError('invalid private state namespace')
    root = data_root if data_root is not None else os.environ.get('DATACORE_ROOT', Path.home() / 'Data')
    if not root or not Path(root).is_absolute():
        raise ValueError('invalid data root for private state')
    excluded = (Path(root).resolve(), Path(__file__).resolve().parent)
    if any(directory.is_relative_to(path) for path in excluded):
        raise ValueError('runtime state cannot be stored in data or installed code')
    target = directory.joinpath(*components)
    # Git ownership, including linked worktrees, is independent of ignore rules.
    for parent in (target, *target.parents):
        if os.path.lexists(parent / '.git'):
            raise ValueError('private runtime state cannot be stored in a Git repository')
    current = Path(directory.anchor)
    for component in target.parts[1:]:
        current /= component
        try:
            current.mkdir(mode=0o700)
            fsync_directory(current.parent)
        except FileExistsError:
            pass
        info = current.lstat()
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('private state directory cannot be a symbolic link or special file')
        if info.st_uid not in (0, os.geteuid()):
            raise ValueError('private state ancestor has an unrelated owner')
        if current.is_relative_to(directory):
            if info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError('runtime state directory must be private to its identity')
        elif info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
            raise ValueError('private state ancestor permits unrelated writes')
    return target


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Write JSON atomically."""
    atomic_write_text(path, json.dumps(data, indent=indent) + "\n")


def atomic_write_yaml(path: Path, data: Any) -> None:
    """Write YAML atomically."""
    import yaml
    content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    atomic_write_text(path, content)


@contextmanager
def file_lock(path: Path, timeout: float = 5.0, *, lock_path: Path | None = None):
    """Advisory file lock using fcntl.flock().

    Creates a stable .lock file next to the target. Lock errors propagate;
    timeout expires before entering the protected body. Never unlink the
    lock file: waiters must continue to coordinate on the same inode.
    An explicit lock_path interoperates with an existing provider's lock
    naming contract; it must be chosen by trusted integration code.
    """
    path = Path(path)
    lock_path = Path(lock_path) if lock_path is not None else path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if timeout < 0:
        raise ValueError("lock timeout must be nonnegative")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(lock_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('lock must be a regular file with one link')
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"timed out acquiring lock for {path}") from None
                time.sleep(min(0.05, remaining))
        yield
    finally:
        os.close(lock_fd)  # closing also releases flock, including on body errors


def locked_read_modify_write_yaml(path: Path, modifier: Callable[[Any], Any]) -> None:
    """Read YAML, apply modifier function, write back atomically under lock.

    Args:
        path: YAML file to modify
        modifier: function that takes current data (or None) and returns new data
    """
    import yaml
    with file_lock(path):
        existing = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f)
        except FileNotFoundError:
            pass

        result = modifier(existing)
        atomic_write_yaml(path, result)


def locked_read_modify_write_json(path: Path, modifier: Callable[[Any], Any]) -> None:
    """Read JSON, apply modifier function, write back atomically under lock."""
    with file_lock(path):
        existing = None
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except FileNotFoundError:
            pass

        result = modifier(existing)
        atomic_write_json(path, result)


def locked_read_modify_write_text(path: Path, modifier: Callable[[str | None], str]) -> None:
    """Preserve raw UTF-8 text while serializing a complete read/modify/write."""
    path = Path(path)
    with file_lock(path):
        try:
            existing = path.read_bytes().decode("utf-8")
        except FileNotFoundError:
            existing = None
        atomic_write_text(path, modifier(existing))
