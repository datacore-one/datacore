"""SPC-8: Memories scoped to one space or project never surface in a session
for a space where they do not belong.

Kind: deterministic + production contract (local, read-only).

Deterministic: a throwaway PLUR store and two tmp spaces, each declaring its
PLUR scope the way PLUR reads it (`.plur.yaml` `scope:` in the session's
directory). The real UserPromptSubmit hook (`plur_inject_wrapper.py` ->
`plur hook-inject`) is sent the same question from each:
  * a memory scoped to project:6-meridian is injected in the 6-meridian
    session and NOT in the 1-datafund session;
  * a global memory is injected in both (control: the hook works there).

Production (@production, local): every space on this machine declares its
PLUR scope (a `.plur.yaml` with `scope:` in the space root) -- without one, a
session there is unscoped and nothing can keep another space's memories out.

Seeded failure: today's inject ignores the session's scope when choosing
engrams (the scope only feeds the "use this scope for plur_learn" hint), so a
6-meridian trading memory surfaces in a 1-datafund session.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
WRAPPER = LIB / "hooks" / "plur_inject_wrapper.py"
ROOT = LIB.parents[1]
QUESTION = "How do trades settle, and what is the house style for commit messages?"


@pytest.fixture
def world(tmp_path):
    plur = shutil.which("plur")
    if not plur:
        pytest.fail("PLUR CLI is not installed (could not check)")
    store, home, tmp, state = (tmp_path / n for n in ("store", "home", "tmp", "state"))
    for d in (home, tmp):
        d.mkdir()
    state.mkdir(mode=0o700)
    shim = tmp_path / "plur-shim"
    shim.write_text(f"#!/bin/sh\nexec '{plur}' --path '{store}' \"$@\"\n")
    shim.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "TMPDIR": str(tmp), "DATACORE_STATE": str(state),
           "DATACORE_PLUR_CLI": str(shim), "DATACORE_HEADLESS": "1"}
    spaces = {}
    for name in ("1-datafund", "6-meridian"):
        d = tmp_path / "Data" / name
        d.mkdir(parents=True)
        (d / ".plur.yaml").write_text(f"scope: project:{name}\n")
        spaces[name] = d
    for statement, scope in (("Meridian trades settle in USDC on Solana at T+0", "project:6-meridian"),
                             ("Commit messages use the imperative mood", None)):
        args = [str(shim), "--json", "--quiet", "learn", statement] + (["--scope", scope] if scope else [])
        r = subprocess.run(args, env=env, cwd=spaces["6-meridian"], capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
    return env, spaces


def _ask(env, cwd, session):
    payload = {"prompt": QUESTION, "session_id": session, "cwd": str(cwd), "hook_event_name": "UserPromptSubmit"}
    r = subprocess.run([sys.executable, str(WRAPPER)], input=json.dumps(payload), env=env, cwd=cwd,
                       capture_output=True, text=True, timeout=55)
    return json.loads(r.stdout or "{}").get("additionalContext") or ""


def test_a_space_scoped_memory_stays_in_its_space(world):
    env, spaces = world
    home = _ask(env, spaces["6-meridian"], "s-meridian")
    away = _ask(env, spaces["1-datafund"], "s-datafund")
    assert "imperative" in home and "imperative" in away, "control failed: the hook injected nothing"
    assert "USDC" in home, f"the scoped memory is missing in its own space: {home[:300]!r}"
    assert "USDC" not in away, f"a project:6-meridian memory surfaced in a 1-datafund session: {away[:400]!r}"


@pytest.mark.production
def test_every_space_here_declares_its_memory_scope():
    spaces = sorted(p for p in ROOT.glob("[0-9]-*") if p.is_dir())
    assert spaces, "no spaces found (could not check)"
    missing = []
    for sp in spaces:
        cfg = sp / ".plur.yaml"
        text = cfg.read_text() if cfg.is_file() else ""
        if "scope:" not in text:
            missing.append(sp.name)
    assert not missing, f"no PLUR scope declared (.plur.yaml scope:) in: {', '.join(missing)}"
