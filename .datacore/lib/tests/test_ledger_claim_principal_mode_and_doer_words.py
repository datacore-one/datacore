"""Claimed items run in the principal's declared mode; a release keeps the doer's words.

Found 2026-11-09 (2-datacore/1-tracks/engineering/reports/2026-11-09-claim-pass-findings-report.md):
* Miles's weekly delegation round failed on every pass. The claim run never
  set DATACORE_CLAUDE_PERMISSION_MODE, so the executor fell back to acceptEdits,
  which refuses every Bash step needing approval -- although principals.yaml
  declares Miles `bypass` and the cadence runner already honours it.
* When a check failed, the release carried only the check's error. The doer's
  reason ("no record of the outage, so writing it would mean inventing it")
  was lost, and the owner was told the check failed, not why.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import ledger_claim  # noqa: E402


class FakeExecutor:
    name = "fake"

    def __init__(self):
        self.env = {}

    def run(self, prompt, **kw):
        self.env = {k: os.environ.get(k) for k in ("DATACORE_CLAUDE_PERMISSION_MODE", "DATACORE_POLICY_PRINCIPAL")}
        return SimpleNamespace(error=None, text="did it", model="m", cost_cents=1)


@pytest.mark.parametrize("declared, expected", [("bypass", "bypassPermissions"),
                                                ("propose", "acceptEdits"), (None, "acceptEdits")])
def test_the_claim_run_uses_the_principals_declared_mode(monkeypatch, tmp_path, declared, expected):
    fake = FakeExecutor()
    monkeypatch.setattr(ledger_claim, "get_executor", lambda: fake)
    monkeypatch.setattr(ledger_claim, "_declared_mode", lambda actor: declared)
    monkeypatch.delenv("DATACORE_CLAUDE_PERMISSION_MODE", raising=False)
    ok, _detail, _meta = ledger_claim.run_task("t", "do", tmp_path, "i1", actor="miles")
    assert ok
    assert fake.env == {"DATACORE_CLAUDE_PERMISSION_MODE": expected, "DATACORE_POLICY_PRINCIPAL": "miles"}


def test_the_mode_does_not_outlive_the_claim_run(monkeypatch, tmp_path):
    """The mode is set for the executor call only: left in os.environ it leaked
    into every later test in the same process (test_executors failed after the
    claim tests ran) and would leak into a later item for another principal."""
    monkeypatch.setattr(ledger_claim, "get_executor", lambda: FakeExecutor())
    monkeypatch.setattr(ledger_claim, "_declared_mode", lambda actor: "bypass")
    monkeypatch.delenv("DATACORE_CLAUDE_PERMISSION_MODE", raising=False)
    monkeypatch.setenv("DATACORE_POLICY_PRINCIPAL", "before")
    ledger_claim.run_task("t", "do", tmp_path, "i1", actor="miles")
    assert "DATACORE_CLAUDE_PERMISSION_MODE" not in os.environ
    assert os.environ["DATACORE_POLICY_PRINCIPAL"] == "before"


def test_a_failed_check_release_carries_the_doers_last_words():
    words = "x" * 2000 + " No record of the outage, so writing it would mean inventing it."
    p = ledger_claim._check_failed_release("i1", "miles", "abc123", "it exited 1", "test -s n.md", words)
    assert p["error"].startswith("check failed: it exited 1")
    assert p["doer_said"].endswith("inventing it.")
    assert len(p["doer_said"]) <= 500
    assert (p["id"], p["owner"], p["artifact_commit"]) == ("i1", "miles", "abc123")
