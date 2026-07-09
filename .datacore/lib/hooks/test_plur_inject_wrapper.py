#!/usr/bin/env python3
"""Verifies the orphan-leak fix in plur_inject_wrapper (datacore-one/datacore#33).

The wrapper must launch hook-inject in its own process group and, on timeout,
kill the WHOLE group — otherwise the CLI's node grandchild orphans to PID 1
and spins forever. This test fakes a command that spawns a grandchild and
asserts the grandchild is dead after a forced timeout.

Run directly:  python3 test_plur_inject_wrapper.py
Or via pytest: pytest test_plur_inject_wrapper.py
"""
import importlib.util
import os
import tempfile
import time
from pathlib import Path

_WRAPPER = Path(__file__).with_name("plur_inject_wrapper.py")


def _load():
    spec = importlib.util.spec_from_file_location("piw", _WRAPPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def test_timeout_kills_whole_process_group():
    mod = _load()
    pidfile = Path(tempfile.gettempdir()) / "plur_test_grandchild.pid"
    if pidfile.exists():
        pidfile.unlink()

    # Fake hook: spawn a long-sleeping grandchild, record its PID, then block.
    # If the wrapper kills only the direct child, the grandchild survives.
    script = f"sleep 60 & echo $! > {pidfile}; sleep 60"
    mod._hook_cmd = lambda: ["sh", "-c", script]

    out = mod._run_hook("", timeout=1)  # forces the timeout path
    assert out == "", f"expected empty output on timeout, got {out!r}"

    # Give the pidfile a moment, then read the grandchild PID.
    for _ in range(20):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        time.sleep(0.05)
    gc_pid = int(pidfile.read_text().strip())

    time.sleep(0.4)  # let the SIGTERM/SIGKILL land
    assert not _alive(gc_pid), f"grandchild {gc_pid} survived — orphan leak NOT fixed"
    pidfile.unlink(missing_ok=True)


if __name__ == "__main__":
    test_timeout_kills_whole_process_group()
    print("PASS: timeout kills the whole process group (no orphan)")
