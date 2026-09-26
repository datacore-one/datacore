"""OPS-2: "A health check says "broken" only for a real fault, and "could not
check" (never OK) when it could not look."

Kind: deterministic. Every check in v2_verify.py (the fleet health checklist)
is run three times against a disposable root with its probes replaced:
  A. every probe answers "all good"            (rc 0, "OK ...")
  B. every probe times out                     (rc 124 / TimeoutExpired)
  C. every probe reports a fault               (rc 1, "... FAILED")
  D. every probe dies on an import error       (rc 1, ModuleNotFoundError)
A row whose verdict differs between A and C depends on what its probe saw. In
B and D the probe did not look, so each such row must read n-a (ok is None):
never FAIL (a false "broken") and never ok (a false "fine").
Plus the OI-03 shape: the job verifier answering "OK 0 jobs" (the host's
identity matched nothing) is "could not check", not OK.

Seeded failure: a check that maps `rc != 0` to FAIL (a timeout becomes
"broken"), or maps "no output" / "0 jobs" to ok.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import v2_verify

CHECKS = [
    "check_ledger", "check_jobs", "check_config", "check_briefing", "check_registry",
    "check_executors", "check_projection", "check_identity", "check_install_current",
    "check_transport", "check_stores", "check_topology", "check_versions",
    "check_trust_labels", "check_hooks", "check_migration_leftovers", "check_finality",
    "check_app", "check_declared_identity", "check_writer_authorship", "check_principals",
    "check_signed_events", "check_egress", "check_fleet",
]

MODES = {
    "good": (0, "OK 3 jobs 3 artifacts\nOK 2 files 10 events\n"),
    "fault": (1, "job 'x' FAILED:\n  - broken\nFAIL\n"),
    "timeout": (124, "timed out after 1s"),
    "importerror": (1, "Traceback (most recent call last):\nModuleNotFoundError: No module named 'yaml'\n"),
}


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "Data"
    for name in ("0-personal", "2-datacore"):
        ev = root / name / ".datacore" / "events"
        ev.mkdir(parents=True)
        (ev / "a.jsonl").write_text('{"seq": 0}\n')
        (root / name / ".git").mkdir()
    (root / ".datacore" / "lib").mkdir(parents=True)
    return root


def _sweep(tmp_path, monkeypatch, mode: str) -> dict[tuple[str, str, str], bool | None]:
    rc, out = MODES[mode]
    monkeypatch.setattr(v2_verify, "ROOT", _root(tmp_path / mode))
    monkeypatch.setattr(v2_verify, "run", lambda args, timeout=180: (rc, out))

    def fake_subprocess_run(args, *a, **kw):
        if mode == "timeout":
            raise subprocess.TimeoutExpired(args, kw.get("timeout") or 1)
        if kw.get("check") and rc != 0:
            raise subprocess.CalledProcessError(rc, args, out, out)
        text = kw.get("text") or kw.get("universal_newlines")
        return subprocess.CompletedProcess(args, rc, out if text else out.encode(),
                                           "" if text else b"")

    monkeypatch.setattr(v2_verify.subprocess, "run", fake_subprocess_run)
    monkeypatch.setattr(v2_verify, "_alias_configured", lambda alias: True, raising=False)
    rows: dict[tuple[str, str, str], bool | None] = {}
    for fn in CHECKS:
        rep = v2_verify.Report()
        f = getattr(v2_verify, fn)
        try:
            f(rep, False) if fn == "check_ledger" else f(rep)
        except Exception as exc:  # noqa: BLE001 - a crashing check is its own finding
            rows[(fn, "CRASH", type(exc).__name__)] = False
            continue
        for c in rep.checks:
            rows.setdefault((fn, c.dip, c.name.split(" (")[0]), c.ok)
    return rows


@pytest.fixture(scope="module")
def sweeps(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    try:
        out = {}
        for mode in MODES:
            out[mode] = _sweep(tmp_path_factory.mktemp("ops2"), mp, mode)
            mp.undo()
        return out
    finally:
        mp.undo()


def _probe_dependent(sweeps) -> list[tuple[str, str, str]]:
    good, fault = sweeps["good"], sweeps["fault"]
    return [k for k in good if k in fault and good[k] != fault[k]]


def test_the_sweep_found_probe_dependent_checks(sweeps):
    # Guards against a vacuous pass: the harness must actually reach the probes.
    assert len(_probe_dependent(sweeps)) >= 3, _probe_dependent(sweeps)


@pytest.mark.parametrize("mode", ["timeout", "importerror"])
def test_a_check_that_could_not_look_says_so(sweeps, mode):
    wrong = []
    for k in _probe_dependent(sweeps):
        got = sweeps[mode].get(k, "absent")
        if got is not None:
            wrong.append(f"{k}: {'FAIL' if got is False else 'ok' if got is True else got}")
    crashes = [k for k in sweeps[mode] if k[1] == "CRASH"]
    assert not wrong and not crashes, (
        f"probes that could not look ({mode}) must read n-a:\n" + "\n".join(wrong)
        + ("\ncrashed: " + str(crashes) if crashes else ""))


def test_zero_jobs_checked_is_not_ok(tmp_path, monkeypatch):
    """OI-03: no identity in cron -> the verifier matched no jobs -> 'OK 0 jobs'."""
    monkeypatch.setattr(v2_verify, "ROOT", _root(tmp_path))
    monkeypatch.setattr(v2_verify, "run", lambda args, timeout=180: (0, "OK 0 jobs 0 artifacts\n"))
    rep = v2_verify.Report()
    v2_verify.check_jobs(rep)
    assert rep.checks and rep.checks[0].ok is None, (
        f"'OK 0 jobs' means nothing was checked, reported {rep.checks[0].ok}: {rep.checks[0].detail}")
