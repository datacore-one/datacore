#!/usr/bin/env python3
"""Wrapper around plur hook-inject that adds forceful session start reminder
if the sentinel file doesn't exist yet.

Runs async (settings.json: async: true, timeout: 90) — the CLI cold-start
loads the BGE embedder (~20s with a 4k-engram store), too slow for a
blocking hook. Inner subprocess timeout stays under the hook's 90s budget.

The child is launched in its own process group (start_new_session=True) so a
timeout kills the whole tree. The CLI spawns a `node … plur hook-inject`
grandchild; killing only the direct child (as subprocess.run's timeout does)
orphaned that grandchild to PID 1, where it kept spinning at high CPU forever
— the orphan leak in datacore-one/datacore#33. A non-blocking per-session
lock stops overlapping invocations from stacking multiple ~20s embedder loads.
"""
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Prefer the plur-init-installed shim (direct node invocation, no npx
# resolution overhead); fall back to npx when it isn't installed.
_SHIM = Path.home() / ".plur" / "bin" / "plur-hook"

# Inner subprocess budget — stays under the hook's 90s async timeout.
_TIMEOUT = 85


def _hook_cmd():
    if _SHIM.exists():
        return [str(_SHIM), "hook-inject"]
    return ["npx", "@plur-ai/cli", "hook-inject"]


def _kill_group(pid):
    """SIGTERM then SIGKILL the child's entire process group, so the CLI's
    node grandchild cannot orphan to PID 1 and keep spinning."""
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, OSError):
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except (ProcessLookupError, OSError):
            return
        time.sleep(0.15)


def _run_hook(stdin_data, timeout=_TIMEOUT):
    """Run hook-inject in its own process group. On timeout, kill the whole
    group and return "" instead of crashing. Returns the child's stdout."""
    try:
        with subprocess.Popen(
            _hook_cmd(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,  # child leads its own process group
        ) as proc:
            try:
                stdout, _ = proc.communicate(input=stdin_data, timeout=timeout)
                return stdout
            except subprocess.TimeoutExpired:
                _kill_group(proc.pid)
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return ""
    except (FileNotFoundError, OSError):
        return ""


def _try_lock(session_id):
    """Non-blocking per-session lock. Returns the held file object on success,
    or None when another inject is already running (skip to avoid pile-up).
    flock releases automatically if the holder dies, so it never goes stale."""
    lock_path = Path(tempfile.gettempdir()) / f"plur-inject-{session_id or 'nosess'}.lock"
    f = None
    try:
        f = open(lock_path, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        if f is not None:
            f.close()
        return None


def main():
    # Read stdin for session_id
    try:
        stdin_data = sys.stdin.read()
        data = json.loads(stdin_data) if stdin_data.strip() else {}
    except (json.JSONDecodeError, EOFError):
        stdin_data = ""
        data = {}

    session_id = data.get("session_id", "")

    # Run the original hook-inject, FORWARDING the hook payload on stdin —
    # hook-inject reads {prompt} from stdin for prompt-aware injection.
    # Swallowing it here (pre-2026-07-06 bug) degraded every injection to
    # a generic 'general session' query.
    #
    # Skip the expensive inject if another one is already in flight for this
    # session (a prior run still loading the embedder) — prevents overlapping
    # ~20s embedder loads from stacking up under rapid tool/prompt bursts.
    lock = _try_lock(session_id)
    if lock is not None:
        try:
            stdout = _run_hook(stdin_data)
        finally:
            lock.close()  # releases flock
    else:
        stdout = ""

    try:
        output = json.loads(stdout) if stdout.strip() else {}
    except json.JSONDecodeError:
        output = {}

    # If session not started, prepend forceful reminder. Skip reminder in
    # headless contexts (chat-sidecar bootstraps the session itself); the
    # injection itself still runs. This runs even when the inject timed out
    # or was skipped, so the session-start guard reminder is never lost.
    sentinel = f"{tempfile.gettempdir()}/plur-session-{session_id}" if session_id else ""
    if sentinel and not os.path.exists(sentinel) and not os.environ.get("DATACORE_HEADLESS"):
        existing = output.get("additionalContext", "")
        reminder = (
            ">>> MANDATORY: Call plur_session_start IMMEDIATELY before any other action. "
            "A PreToolUse guard will block all tools until you do. <<<"
        )
        output["additionalContext"] = f"{reminder}\n\n{existing}" if existing else reminder

    print(json.dumps(output))


if __name__ == "__main__":
    main()
