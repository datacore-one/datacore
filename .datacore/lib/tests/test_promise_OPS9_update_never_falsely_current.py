"""OPS-9: agent hosts update themselves to a known-good state or roll back, and
never falsely report "already current".

2026-09-26 on hermes: a half-renamed npm folder broke `npm ls`, the updater
read no npm packages at all, compared only what it saw, and logged "already
current" while npm had failed on every run since 2026-09-20.

Seeded failure: treat an unreadable package list as "no packages" again.
"""
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

import agent_update as U  # noqa: E402


def _run(monkeypatch, tmp_path, before, after):
    snaps = iter([before, after])
    logged = []
    monkeypatch.setattr(U, "STATE_DIR", tmp_path)
    monkeypatch.setattr(U, "snapshot", lambda prof: next(snaps))
    monkeypatch.setattr(U, "install", lambda *a, **k: None)
    monkeypatch.setattr(U, "log", logged.append)
    host = sorted(U.PROFILES)[0]
    monkeypatch.setattr(sys, "argv", ["agent_update.py", "--host", host, "--apply"])
    return U.main(), logged, U._expand(U.PROFILES[host])


def test_an_unreadable_package_list_is_never_already_current(monkeypatch, tmp_path):
    prof = U._expand(U.PROFILES[sorted(U.PROFILES)[0]])
    pip = {p: "1.0" for p in prof["pip_packages"]}
    rc, logged, _ = _run(monkeypatch, tmp_path, {"pip": pip, "npm": {}}, {"pip": pip, "npm": {}})
    if not prof["npm_packages"]:
        return   # a host with no npm packages cannot show this failure
    assert rc != 0
    assert not any("already current" in line for line in logged)
    assert any("could not read" in line for line in logged)


def test_a_real_no_op_is_still_already_current(monkeypatch, tmp_path):
    prof = U._expand(U.PROFILES[sorted(U.PROFILES)[0]])
    snap = {"pip": {p: "1.0" for p in prof["pip_packages"]},
            "npm": {p: "1.0" for p in prof["npm_packages"]}}
    rc, logged, _ = _run(monkeypatch, tmp_path, snap, snap)
    assert rc == 0 and any("already current" in line for line in logged)
