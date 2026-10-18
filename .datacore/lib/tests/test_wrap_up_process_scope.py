"""Preflight stops only this session's dev servers (2026-10-18).

A wrap-up's preflight killed four vitest/tsup runs that belonged to a different
Claude session working in its own scratchpad: the rule assumed any dev server
with a live parent "belongs to this session, which is ending anyway". Two
Claude sessions on one machine share every ancestor above their own CLI
process, so ownership is decided by the nearest `claude` ancestor of the
wrap-up itself. A live-parent dev server under another session's CLI is
flagged, never killed. Orphans (ppid=1) are still killed.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wrap_up_mechanics as wm  # noqa: E402

PS = """  PID  PPID     ELAPSED COMMAND
    1     0  10-00:00:00 /sbin/launchd
   50     1  10-00:00:00 /Applications/Terminal.app/Contents/MacOS/Terminal
  100    50     01:00:00 claude
  200   100        00:10 /bin/zsh -c python3 wrap_up_mechanics.py preflight
  300   200        00:05 python3 wrap_up_mechanics.py preflight
  400   100        02:00 node /tmp/mine/node_modules/.bin/vitest run
  500    50     01:00:00 claude
  550   500        01:00 /bin/zsh -c npm test
  600   550        00:30 node /tmp/theirs/node_modules/.bin/vitest run
  700     1     05:00:00 node /tmp/old/node_modules/.bin/vite
"""


def _scan(monkeypatch):
    monkeypatch.setattr(wm, "_run", lambda cmd, **kw: (0, PS, ""))
    monkeypatch.setattr(wm.os, "getpid", lambda: 300)
    return wm.scan_processes()


def test_a_dev_server_under_this_sessions_claude_is_killed(monkeypatch):
    res = _scan(monkeypatch)
    assert 400 in [p["pid"] for p in res["kill"]]


def test_a_dev_server_under_another_sessions_claude_is_flagged_not_killed(monkeypatch):
    res = _scan(monkeypatch)
    assert 600 not in [p["pid"] for p in res["kill"]]
    assert 600 in [p["pid"] for p in res["flag"]]


def test_an_orphaned_dev_server_is_still_killed(monkeypatch):
    res = _scan(monkeypatch)
    assert 700 in [p["pid"] for p in res["kill"]]


def test_with_no_claude_ancestor_no_live_parent_dev_server_is_killed(monkeypatch):
    ps = PS.replace("  100    50     01:00:00 claude", "  100    50     01:00:00 /bin/bash")
    monkeypatch.setattr(wm, "_run", lambda cmd, **kw: (0, ps, ""))
    monkeypatch.setattr(wm.os, "getpid", lambda: 300)
    res = wm.scan_processes()
    killed = [p["pid"] for p in res["kill"]]
    assert 400 not in killed and 600 not in killed and 700 in killed
