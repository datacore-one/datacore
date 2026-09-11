#!/usr/bin/env python3
"""Shared file utilities for Datacore — atomic writes and advisory locking.

All write operations use mkstemp + os.replace for crash safety.
Advisory locking via fcntl.flock() prevents concurrent session corruption.
"""
import fcntl
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import yaml


def _log(msg: str):
    print(f"[file_utils] {msg}", file=sys.stderr)


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


def fsync_directory(path: Path) -> None:
    """Persist directory entry changes (create/rename) on supported hosts."""
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, data: Any, indent: int = 2) -> None:
    """Write JSON atomically."""
    atomic_write_text(path, json.dumps(data, indent=indent) + "\n")


def atomic_write_yaml(path: Path, data: Any) -> None:
    """Write YAML atomically."""
    content = yaml.safe_dump(data, default_flow_style=False, sort_keys=False, allow_unicode=True)
    atomic_write_text(path, content)


@contextmanager
def file_lock(path: Path, timeout: float = 5.0):
    """Advisory file lock using fcntl.flock().

    Creates a stable .lock file next to the target. Lock errors propagate;
    timeout expires before entering the protected body. Never unlink the
    lock file: waiters must continue to coordinate on the same inode.
    """
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if timeout < 0:
        raise ValueError("lock timeout must be nonnegative")
    lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
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
