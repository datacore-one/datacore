"""The incremental verify's marker lives in private runtime state, by the same
rules as every other runtime state (file_utils.private_state_directory): the
marker file is private to its user, and a state path that is an alias is
refused -- verify still answers, it just cannot remember (Phase 2, A#8)."""
import os

from ledger.log import EventLog
from ledger.verify import _marker_path, verify_log


def _log(tmp_path, monkeypatch, state):
    monkeypatch.setenv("DATACORE_STATE", str(state))
    monkeypatch.setenv("DATACORE_ROOT", str(tmp_path / "Data"))
    space = tmp_path / "Data" / "team"
    EventLog(space, "alice", sign=False).append("item.create", {"id": "t-1", "title": "x"})
    return space / ".datacore" / "events" / "alice.jsonl"


def test_the_marker_file_is_private(tmp_path, monkeypatch):
    log = _log(tmp_path, monkeypatch, tmp_path / "home" / ".datacore" / "state")
    assert verify_log(log, incremental=True).verdict == "ok"
    marker = _marker_path(log)
    assert marker.exists(), "no marker written; the case is not exercised"
    assert not marker.stat().st_mode & 0o077, f"marker readable by others: {oct(marker.stat().st_mode & 0o777)}"


def test_an_aliased_state_path_is_refused_and_verify_still_answers(tmp_path, monkeypatch):
    real = tmp_path / "real-state"
    real.mkdir(mode=0o700)
    alias = tmp_path / "state-alias"
    os.symlink(real, alias)
    log = _log(tmp_path, monkeypatch, alias)
    report = verify_log(log, incremental=True)
    assert report.verdict == "ok", report.problems
    assert not any(real.rglob("*.json")), "a marker was written through an aliased state path"
