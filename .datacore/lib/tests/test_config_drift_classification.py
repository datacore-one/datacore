"""A lost connection is not a missing directory.

config_drift's second probe discarded its exit status, so a dropped ssh call
and an absent hooks directory both produced an empty listing and both read as
"missing-dir". On 2026-09-17 that reported nightshift's hooks missing during a
45-second maintenance wake on a lid-closed laptop; they were present.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB / "detectors"))

import config_drift  # noqa: E402

HOOKS = "/srv/datacore/.datacore/githooks"


def _scripted(monkeypatch, listing_results):
    """First probe always answers; the listing returns each scripted result in turn."""
    calls = iter(listing_results)
    monkeypatch.setattr(config_drift.time if hasattr(config_drift, "time") else __import__("time"),
                        "sleep", lambda *_: None)

    def fake_run(host, user, cmd):
        if "core.hooksPath" in cmd:
            return 0, HOOKS
        if cmd == "true":
            return 0, ""
        return next(calls)
    monkeypatch.setattr(config_drift, "run", fake_run)


def test_the_2026_09_17_shape_a_dropped_listing_is_not_drift(monkeypatch):
    _scripted(monkeypatch, [(255, ""), (0, "pre-commit pre-push post-merge ")])
    assert config_drift.check("nightshift", "nightshift", None)["status"] == "ok"


def test_a_listing_that_keeps_failing_is_unreachable_not_missing(monkeypatch):
    _scripted(monkeypatch, [(255, ""), (255, "")])
    assert config_drift.check("nightshift", "nightshift", None)["status"] == "unreachable"


def test_a_directory_that_is_really_absent_is_still_reported(monkeypatch):
    _scripted(monkeypatch, [(3, "")])
    assert config_drift.check("nightshift", "nightshift", None)["status"] == "missing-dir"


def test_missing_required_hooks_are_still_reported(monkeypatch):
    _scripted(monkeypatch, [(0, "post-merge ")])
    assert config_drift.check("nightshift", "nightshift", None)["status"] == "missing-hooks"


# ── a dropped first probe is not "unconfigured" ──────────────────────────────


def _first_probe(monkeypatch, results):
    """Script the hooksPath probe; `true` always answers, as it did on 09-18."""
    calls = iter(results)
    monkeypatch.setattr(__import__("time"), "sleep", lambda *_: None)

    def fake_run(host, user, cmd):
        if cmd == "true":
            return 0, ""
        if "core.hooksPath" in cmd:
            return next(calls)
        return 0, "pre-commit pre-push "
    monkeypatch.setattr(config_drift, "run", fake_run)


def test_a_dropped_call_is_never_read_as_unconfigured(monkeypatch):
    """2026-09-18 14:54Z: a laptop wake left three hosts unreachable and plur-claw
    reporting 'core.hooksPath not configured'. The next manual run showed it
    configured. The liveness probe (`ssh host true`) can succeed a moment after
    the real probe was dropped, and the detector believed the silence."""
    _first_probe(monkeypatch, [(config_drift.TRANSPORT, "TimeoutExpired: ..."),
                               (config_drift.TRANSPORT, "TimeoutExpired: ...")])
    assert config_drift.check("plur-claw", "plur-claw", None)["status"] == "unreachable"


def test_ssh_connection_failure_is_unreachable_not_unconfigured(monkeypatch):
    _first_probe(monkeypatch, [(255, ""), (255, "")])
    assert config_drift.check("plur-claw", "plur-claw", None)["status"] == "unreachable"


def test_a_dropped_call_that_succeeds_on_retry_reports_the_truth(monkeypatch):
    _first_probe(monkeypatch, [(config_drift.TRANSPORT, "OSError: ..."), (0, HOOKS)])
    result = config_drift.check("plur-claw", "plur-claw", None)
    assert result["status"] == "ok" and result["detail"] == HOOKS


def test_git_saying_the_key_is_unset_is_still_a_finding(monkeypatch):
    """The machine answering 'not set' is a real answer -- git exits 1 with no
    output -- and must keep reporting as drift."""
    _first_probe(monkeypatch, [(1, ""), (1, "")])
    result = config_drift.check("plur-claw", "plur-claw", None)
    assert result["status"] == "unset"
