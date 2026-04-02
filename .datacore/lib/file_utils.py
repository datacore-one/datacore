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
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import yaml


def _log(msg: str):
    print(f"[file_utils] {msg}", file=sys.stderr)


def atomic_write_text(path: Path, content: str) -> None:
    """Write text atomically: mkstemp in same dir, then os.replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode("utf-8"))
        os.close(fd)
        fd = -1
        os.replace(tmp_path, path)
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

    Creates a .lock file next to the target. Yields with lock held.
    Falls back to no-lock with a warning if fcntl is unavailable.
    """
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = None
    try:
        lock_fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        yield
    except (ImportError, OSError) as e:
        _log(f"file lock unavailable for {path}: {e}")
        yield  # fall back to unlocked
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)
            except OSError:
                pass


def locked_read_modify_write_yaml(path: Path, modifier: Callable[[Any], Any]) -> None:
    """Read YAML, apply modifier function, write back atomically under lock.

    Args:
        path: YAML file to modify
        modifier: function that takes current data (or None) and returns new data
    """
    with file_lock(path):
        existing = None
        if path.exists():
            try:
                with open(path, "r") as f:
                    existing = yaml.safe_load(f)
            except Exception as e:
                _log(f"failed to read {path}: {e}")

        result = modifier(existing)
        atomic_write_yaml(path, result)


def locked_read_modify_write_json(path: Path, modifier: Callable[[Any], Any]) -> None:
    """Read JSON, apply modifier function, write back atomically under lock."""
    with file_lock(path):
        existing = None
        if path.exists():
            try:
                with open(path, "r") as f:
                    existing = json.load(f)
            except Exception as e:
                _log(f"failed to read {path}: {e}")

        result = modifier(existing)
        atomic_write_json(path, result)
