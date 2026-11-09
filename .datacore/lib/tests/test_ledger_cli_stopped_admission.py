"""`ledger_cli stopped` also names a log the hub stops admitting.

On 2026-10-28 Winston wrote two events of a retired type (`task.create`) into
his own log with a stale copy of the ledger library. Hub admission stopped his
log there, so every later claim, release and seal of his counted for nothing,
and `stopped` -- the check a job runs before writing -- said all was well,
because it only read stale-log stop records. A log admission refuses is just
as stopped, so it is listed too, with the reason, and the exit is 3.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

import ledger_cli
from tests import test_ledger_hub as T
from tests.test_ledger_hub_round5 import env  # noqa: F401
from ledger import hub as H


def _stopped(space: Path, capsys) -> tuple[int, str]:
    """`ledger_cli stopped`, in this process: it reads the test's key registry."""
    with pytest.raises(SystemExit) as exit_:
        ledger_cli.cmd_stopped(argparse.Namespace(space=str(space)))
    return exit_.value.code, capsys.readouterr().out


def test_stopped_names_a_log_admission_refuses(env, capsys, monkeypatch):
    import ledger.keys as keys
    monkeypatch.setattr(keys, "DEFAULT_REGISTRY_PATH", env.registry)   # the test's signing keys
    h = env.hub(eligibility=None)
    wa = env.writer("wa", H.LocalTransport(h))
    wa.append("item.create", {"id": "t", "assignee": "wa"})
    space = wa.space_dir
    code, out = _stopped(space, capsys)
    assert code == 0, out

    T._signed_into(env, wa, "task.create", {"id": "x", "title": "written by a stale library"})
    code, out = _stopped(space, capsys)
    assert code == 3, out
    assert "wa.jsonl" in out and "task.create" in out and "admission" in out, out
