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
import shlex
import time
from pathlib import Path

# Named from lib/hooks/, not from beside this file. The test used to live in
# lib/hooks/ and found its subject with with_name(); it moved into lib/tests/ so
# that CI, which gates directories, actually runs it.
_WRAPPER = Path(__file__).resolve().parents[1] / "hooks" / "plur_inject_wrapper.py"


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


def test_timeout_kills_whole_process_group(tmp_path):
    mod = _load()
    pidfile = tmp_path / "grandchild.pid"
    if pidfile.exists():
        pidfile.unlink()

    # Fake hook: spawn a long-sleeping grandchild, record its PID, then block.
    # If the wrapper kills only the direct child, the grandchild survives.
    script = f"sleep 60 & echo $! > {shlex.quote(str(pidfile))}; sleep 60"
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
    with tempfile.TemporaryDirectory() as directory:
        test_timeout_kills_whole_process_group(Path(directory))
    print("PASS: timeout kills the whole process group (no orphan)")


def _main_with_hook_output(monkeypatch, capsys, hook_stdout, session="s-1", headless=True):
    import io
    import json
    mod = _load()
    monkeypatch.setattr(mod, "_run_hook", lambda stdin, rehydrate=False: hook_stdout)
    monkeypatch.setattr(mod, "_try_lock", lambda sid: io.StringIO())
    if headless:
        monkeypatch.setenv("DATACORE_HEADLESS", "1")
    else:
        monkeypatch.delenv("DATACORE_HEADLESS", raising=False)
        monkeypatch.setattr(mod, "_session_marked", lambda sid: False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"session_id": session, "prompt": "q"})))
    mod.main([])
    return json.loads(capsys.readouterr().out)


def test_nested_hook_context_is_also_given_at_the_top_level(monkeypatch, capsys):
    """PLUR's hook-inject (0.22) nests its context under hookSpecificOutput. The
    wrapper's contract -- read by promise SPC-6 and by the reminder merge below --
    is a top-level additionalContext; it must carry the memories too."""
    nested = '{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "[ENG-1] USDC"}}'
    out = _main_with_hook_output(monkeypatch, capsys, nested)
    assert out["additionalContext"] == "[ENG-1] USDC"
    assert out["hookSpecificOutput"]["additionalContext"] == "[ENG-1] USDC"


def test_the_session_reminder_joins_the_memories_in_both_places(monkeypatch, capsys):
    nested = '{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "[ENG-1] USDC"}}'
    out = _main_with_hook_output(monkeypatch, capsys, nested, headless=False)
    for ctx in (out["additionalContext"], out["hookSpecificOutput"]["additionalContext"]):
        assert ctx.startswith("Call plur_session_start") and ctx.endswith("[ENG-1] USDC")
