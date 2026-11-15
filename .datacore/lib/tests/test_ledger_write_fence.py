"""No test, and no process a test starts, writes into the real installation's ledger.

Found 2026-11-15. 1,170 events in four real logs carried signatures no key in
the fleet could verify -- because the test suite signs with a throwaway key per
test, while code under test still wrote into the real spaces: job_verify filed
its alert task into the real system inbox (its TASK_FILE is fixed at import),
and attestation writers reached ROOT/<system space> directly or through a child
process that inherited DATACORE_ROOT. The in-process patches in conftest.py
cannot reach a child process; an environment fence on the one write path can.
"""
import os
from pathlib import Path

import pytest

from ledger.log import EventLog, LedgerWriteFenced


def _space(root: Path, name: str) -> Path:
    s = root / name
    (s / ".datacore" / "events").mkdir(parents=True)
    return s


def test_a_write_under_the_fenced_root_is_refused_and_nothing_is_written(tmp_path, monkeypatch):
    real = tmp_path / "real-install"
    space = _space(real, "2-team")
    monkeypatch.setenv("DATACORE_LEDGER_WRITE_FENCE", str(real))
    with pytest.raises(LedgerWriteFenced):
        EventLog(space, "fixture", sign=False).append("item.create", {"id": "t", "title": "x"})
    assert not any((space / ".datacore" / "events").iterdir())


def test_a_write_outside_the_fence_goes_through(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_WRITE_FENCE", str(tmp_path / "real-install"))
    space = _space(tmp_path / "scratch", "2-team")
    EventLog(space, "fixture", sign=False).append("item.create", {"id": "t", "title": "x"})
    assert (space / ".datacore" / "events" / "fixture.jsonl").exists()


def test_the_suite_fences_this_installation_for_every_test():
    """conftest.py sets the fence to the install around this checkout, and a
    child process inherits it."""
    fence = os.environ.get("DATACORE_LEDGER_WRITE_FENCE")
    assert fence and Path(fence).resolve() == Path(__file__).resolve().parents[3]
