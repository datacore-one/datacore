"""An owner's request reaches its doer fully briefed, and its pickup is visible fast.

2026-11-08, before the owner's final Winston test (owner: "fix risks, then I test"):

1. The agent was told the title and body only. Winston's filings carry done_when
   and check, not a body, so the doer never saw which file to write or what the
   check tests; it learned the check only from a failed attempt. Miles: 38 claims,
   17 releases, 4 completions in five days.
2. The claim was appended before the work but published only by the converge
   after the work, so the watcher on Winston saw no claim for the whole run and
   AGT-14's 15-minute "not picked up" fired on requests that were being worked.
3. Winston's filing waited for his next scheduled converge to leave the pod.
4. The watcher read its own copy, synced on the same quarter hour it runs at.
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import ledger_claim as lc  # noqa: E402


# 1 -- the brief ---------------------------------------------------------------

def test_the_agent_is_told_done_when_and_the_check():
    text = lc._task_text("Incident note", {"done_when": "note at 1-tracks/ops/x.md with ## Cause",
                                           "check": "test -s 1-tracks/ops/x.md && grep -q '## Cause' 1-tracks/ops/x.md"})
    assert "note at 1-tracks/ops/x.md with ## Cause" in text
    assert "grep -q '## Cause' 1-tracks/ops/x.md" in text
    assert "root of this space" in text and "commit" in text.lower()


def test_a_task_without_done_when_or_check_reads_as_before():
    assert lc._task_text("T", {"body": "B"}) == "T\n\nB"


# 2 -- the claim is published before the work ---------------------------------

def test_publish_claim_skips_a_space_with_no_remote(tmp_path, monkeypatch):
    import subprocess
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    called = []
    monkeypatch.setattr(lc.subprocess, "run", lambda *a, **k: called.append(a) or types.SimpleNamespace(returncode=1, stdout=""))
    assert lc._publish_claim(tmp_path) == "skipped"


def test_publish_claim_converges_a_space_with_a_remote(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(returncode=0, stdout="git@example:x.git\n")
    monkeypatch.setattr(lc.subprocess, "run", fake_run)
    assert lc._publish_claim(tmp_path) == "published"
    assert any("converge" in c for c in calls[-1]) and str(tmp_path) in calls[-1]


def test_publish_claim_never_raises(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(lc.subprocess, "run", boom)
    assert lc._publish_claim(tmp_path) == "failed"


@pytest.fixture
def fleet(monkeypatch):
    import actor_identity
    import ledger.policy
    from delegation_drill import DelegationDrill, _install_local_executor
    from ledger_chaos_drill import scratch_fleet
    monkeypatch.setattr(ledger.policy, "DEFAULT_POLICY_PATH", ledger.policy.DEFAULT_POLICY_PATH)
    monkeypatch.setattr(actor_identity, "PRINCIPALS", actor_identity.PRINCIPALS)
    saved = dict(os.environ)
    try:
        with scratch_fleet(prefix="pickup-") as root:
            drill = DelegationDrill(root)
            _install_local_executor()
            yield drill.delegation_space("2-datacore"), drill
    finally:
        os.environ.clear()
        os.environ.update(saved)


def test_the_claim_is_published_before_the_agent_starts(fleet, monkeypatch):
    space, drill = fleet
    order = []
    monkeypatch.setattr(lc, "_publish_claim", lambda s: order.append("publish") or "published")
    real = lc.run_task

    def run(*a, **k):
        order.append("work")
        return real(*a, **k)
    monkeypatch.setattr(lc, "run_task", run)
    drill.delegate(space, by="winston", to="miles", title="write K into k.txt",
                   check="test -f k.txt", asked_by="gregor")
    drill.dispatch(space, "miles")
    assert order[:2] == ["publish", "work"], order


# 4 -- the watcher refreshes a space with an open request before judging -------

def test_the_watcher_refreshes_spaces_with_open_requests(monkeypatch, tmp_path):
    import delegation_watch as dw
    req = types.SimpleNamespace(status="created", payload={"asked_by": "gregor"})
    done = types.SimpleNamespace(status="verified", payload={"asked_by": "gregor"})
    folded = {"2-datacore": types.SimpleNamespace(items={"a": req}),
              "5-plur": types.SimpleNamespace(items={"b": done})}
    seen = []
    monkeypatch.setattr(dw, "_converge", lambda path: seen.append(Path(path).name) or True)
    assert dw.refresh_open_requests(tmp_path, folded) == ["2-datacore"]
    assert seen == ["2-datacore"]
