"""Suite-wide isolation.

DATACORE_STATE is redirected to a per-test tmp dir for EVERY test here.
Without it, test_job_verify.py -- which calls job_verify.main() in-process --
wrote eleven invented job names into ~/.datacore/state/job-verify-recurrence.json
(log-job x18, telegram-job x18, ...), inflating the "recurring" summary that
the production alerts read. recurrence.py is the only consumer of the variable
(verified 2026-09-03), so redirecting it cannot starve a test of real state.
"""
import pytest


@pytest.fixture(autouse=True)
def _isolated_datacore_state(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_STATE", str(tmp_path / "state"))
