"""Preservation, interruption and concurrent-update invariants for shared IO."""
import errno
import fcntl
import json
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import sys

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parents[1]))
import file_utils as files


def _increment(path):
    files.locked_read_modify_write_json(Path(path), lambda value: (value or 0) + 1)


@pytest.mark.parametrize("kind", ["json", "yaml"])
def test_unreadable_state_is_never_replaced(tmp_path, monkeypatch, kind):
    path = tmp_path / kind
    original = b'{"retained": true}'
    path.write_bytes(original)
    modify = getattr(files, f"locked_read_modify_write_{kind}")
    calls = []
    real_open = open

    def denied(name, *args, **kwargs):
        if Path(name) == path:
            raise PermissionError("injected read failure")
        return real_open(name, *args, **kwargs)

    monkeypatch.setattr(files, "open", denied, raising=False)
    with pytest.raises(PermissionError):
        modify(path, lambda value: calls.append(value) or {})
    assert not calls
    assert path.read_bytes() == original


@pytest.mark.parametrize("kind,error", [("json", json.JSONDecodeError), ("yaml", yaml.YAMLError)])
def test_malformed_state_is_preserved(tmp_path, kind, error):
    path = tmp_path / kind
    original = b'{"unfinished": ['
    path.write_bytes(original)
    with pytest.raises(error):
        getattr(files, f"locked_read_modify_write_{kind}")(path, lambda _: {})
    assert path.read_bytes() == original


def test_lock_failure_does_not_enter_body(tmp_path, monkeypatch):
    def fail(*args):
        raise OSError(errno.EIO, "injected lock failure")
    monkeypatch.setattr(files.fcntl, "flock", fail)
    entered = []
    with pytest.raises(OSError, match="injected lock failure"):
        with files.file_lock(tmp_path / "state"):
            entered.append(True)
    assert not entered


def test_body_error_propagates_unchanged_and_releases_lock(tmp_path):
    path = tmp_path / "state"
    failure = OSError(errno.ENOSPC, "injected full disk")
    with pytest.raises(OSError) as caught:
        with files.file_lock(path):
            raise failure
    assert caught.value is failure
    with files.file_lock(path, timeout=0):
        pass


def test_lock_timeout(tmp_path):
    path = tmp_path / "state"
    with open(tmp_path / ".state.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with pytest.raises(TimeoutError):
            with files.file_lock(path, timeout=0):
                pytest.fail("entered contended lock")


def test_short_writes_preserve_entire_unicode_content(tmp_path, monkeypatch):
    path = tmp_path / "data"
    real_write = os.write
    monkeypatch.setattr(files.os, "write", lambda fd, data: real_write(fd, data[:3]))
    files.atomic_write_text(path, "a€🙂z" * 100)
    assert path.read_text() == "a€🙂z" * 100


def test_zero_progress_write_fails_preserving_original(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.write_text("retained")
    monkeypatch.setattr(files.os, "write", lambda fd, data: 0)
    with pytest.raises(OSError):
        files.atomic_write_text(path, "replacement")
    assert path.read_text() == "retained"
    assert not list(tmp_path.glob("*.tmp"))


def test_flush_failure_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.write_text("retained")
    def fail(fd):
        raise OSError(errno.EIO, "injected flush failure")
    monkeypatch.setattr(files.os, "fsync", fail)
    with pytest.raises(OSError, match="flush failure"):
        files.atomic_write_text(path, "replacement")
    assert path.read_text() == "retained"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_flushes_file_before_publish_and_directory_after(tmp_path, monkeypatch):
    calls = []
    real_sync, real_replace = os.fsync, os.replace
    def sync(fd):
        calls.append("sync")
        real_sync(fd)
    def replace(src, dst):
        calls.append("replace")
        real_replace(src, dst)
    monkeypatch.setattr(files.os, "fsync", sync)
    monkeypatch.setattr(files.os, "replace", replace)
    files.atomic_write_text(tmp_path / "data", "new")
    assert calls == ["sync", "replace", "sync"]


def test_concurrent_updates_preserve_every_increment(tmp_path):
    path = tmp_path / "count.json"
    with ProcessPoolExecutor(max_workers=4) as pool:
        list(pool.map(_increment, [str(path)] * 32))
    assert json.loads(path.read_text()) == 32
