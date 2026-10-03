"""A full disk is named as a full disk, never as "offline" (fleet sim 2026-10-03, break 4).

The harsh fleet week filled the overnight host's disk for three hours (fault F18,
a real ENOSPC). The overnight run said `2-datacore -- fetch failed (offline?)` and
`Ledger projection could not be verified (RuntimeError)`, then crashed in the queue
builder with `OSError: [Errno 28] No space left on device`. The owner would have read
"the overnight host is offline". The fix: every place that saw the symptom says
"disk full on <host>, N% used".
"""
from __future__ import annotations

import errno
import shutil
import sys
from collections import namedtuple
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))
NS_LIB = LIB.parent / "modules" / "nightshift" / "lib"

import check_diagnosis  # noqa: E402
import ledger_transport as lt  # noqa: E402

Usage = namedtuple("Usage", "total used free")
FULL = Usage(total=100 * 2**30, used=100 * 2**30, free=0)
ROOMY = Usage(total=100 * 2**30, used=40 * 2**30, free=60 * 2**30)

ENOSPC_FETCH = ("error: unable to create temporary file: No space left on device\n"
                "fatal: failed to write object")


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "nightshift")
    return "nightshift"


def test_disk_full_names_the_host_and_the_percentage(monkeypatch, host, tmp_path):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: FULL)
    said = check_diagnosis.disk_full(tmp_path)
    assert said is not None
    assert f"disk full on {host}" in said and "100% used" in said, said


def test_a_disk_with_room_is_not_called_full(monkeypatch, host, tmp_path):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: ROOMY)
    assert check_diagnosis.disk_full(tmp_path) is None


def test_enospc_text_is_named_even_when_usage_cannot_be_read(monkeypatch, host, tmp_path):
    def boom(p):
        raise OSError(errno.EIO, "cannot stat")
    monkeypatch.setattr(shutil, "disk_usage", boom)
    said = check_diagnosis.disk_full(tmp_path, ENOSPC_FETCH)
    assert said and f"disk full on {host}" in said, said


def test_a_fetch_that_hit_a_full_disk_is_not_offline(monkeypatch, host, tmp_path):
    monkeypatch.setattr(shutil, "disk_usage", lambda p: FULL)
    reason = lt._fetch_reason(ENOSPC_FETCH, tmp_path)
    assert f"disk full on {host}" in reason and "% used" in reason, reason
    assert "offline" not in reason and "fetch failed" not in reason, reason


def test_the_sweep_reads_a_full_disk_as_blocked_not_offline(monkeypatch, host, tmp_path):
    """sync_repo's word for it: offline clears itself, a full disk does not."""
    monkeypatch.setattr(shutil, "disk_usage", lambda p: FULL)
    monkeypatch.setattr(lt, "classify", lambda space, root=None: lt.Result(True, "knowledge", {}))
    monkeypatch.setattr(lt, "converge", lambda space, root=None: lt.Result(
        False, lt._fetch_reason(ENOSPC_FETCH, tmp_path), {}))
    assert lt.sync_repo(tmp_path, quiet=True) == "blocked"


def test_an_unclassified_fetch_failure_keeps_gits_own_words():
    """'offline?' is a guess; git's line says what actually happened."""
    reason = lt._fetch_reason("fatal: unable to access 'https://github.com/x/y.git/': "
                              "Failed to connect to github.com port 443: Connection timed out")
    assert "fetch failed" in reason and "github.com port 443" in reason, reason


def _run_module():
    sys.path.insert(0, str(NS_LIB))
    import run  # noqa: WPS433
    return run


def test_the_overnight_run_refuses_on_a_full_disk_and_says_so(monkeypatch, host, tmp_path, capsys):
    run = _run_module()
    monkeypatch.setattr(shutil, "disk_usage", lambda p: FULL)
    said = run._disk_preflight(tmp_path)
    assert said and f"disk full on {host}" in said and "100% used" in said, said
    assert "nothing run" in said


def test_the_overnight_run_proceeds_when_there_is_room(monkeypatch, host, tmp_path):
    run = _run_module()
    monkeypatch.setattr(shutil, "disk_usage", lambda p: ROOMY)
    assert run._disk_preflight(tmp_path) is None


def test_a_projection_failure_on_a_full_disk_names_the_disk(monkeypatch, host, tmp_path):
    run = _run_module()
    monkeypatch.setattr(shutil, "disk_usage", lambda p: FULL)
    exc = OSError(errno.ENOSPC, "No space left on device")
    msg = run._projection_failure(exc, tmp_path)
    assert f"disk full on {host}" in msg, msg
    assert "RuntimeError" not in msg


def test_a_projection_failure_with_room_still_names_its_class(monkeypatch, host, tmp_path):
    run = _run_module()
    monkeypatch.setattr(shutil, "disk_usage", lambda p: ROOMY)
    msg = run._projection_failure(RuntimeError("x"), tmp_path)
    assert "RuntimeError" in msg and "disk full" not in msg
