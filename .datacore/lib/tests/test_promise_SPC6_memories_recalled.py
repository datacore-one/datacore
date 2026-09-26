"""SPC-6: At the start of a session and before answering a factual question,
relevant memories are recalled and used.

Kind: deterministic + production config. Agent part ("used") pending, see below.

Deterministic: the real UserPromptSubmit hook (`hooks/plur_inject_wrapper.py`
-> installed `plur hook-inject`) run against a throwaway PLUR store (HOME,
TMPDIR, DATACORE_STATE in tmp; DATACORE_PLUR_CLI = a shim adding `--path`):
  * the first prompt of a session gets the memory relevant to it;
  * a later factual question in the SAME session gets the memory relevant to
    that question (not only what was injected at session start);
  * an unrelated memory is not injected for either.

Production (@production, local config): the Claude Code settings on this
machine register the wrapper on UserPromptSubmit and a PLUR hook on
SessionStart, and the PLUR CLI the wrapper resolves is installed.

Agent part ("and used"): pending -- the agent_eval harness builds the child's
HOME from scratch, so a child session has no PLUR hooks wired; grading use
needs the harness to install them into the scaffold first.

Seeded failure: recall only at session start (hook-inject's own per-process
marker makes every later prompt a reminder, not a recall) -- the second
question gets no memory.
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

MEMORIES = [
    ("Meridian trades settle in USDC on Solana", "global"),
    ("The verity repo uses pnpm as its package manager, never npm", "global"),
    ("Gregor's favourite pizza topping is anchovies", "global"),
]


@pytest.fixture
def plur_env(tmp_path):
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
    work = tmp_path / "Data"
    work.mkdir()
    (work / ".plur.yaml").write_text("scope: project:fixture\n")
    for statement, _ in MEMORIES:
        r = subprocess.run([str(shim), "--json", "--quiet", "learn", statement], env=env,
                           capture_output=True, text=True, timeout=30, cwd=work)
        assert r.returncode == 0, r.stderr
    return env, work


def _prompt(env: dict, work: Path, text: str, session: str = "sess-1") -> str:
    payload = {"prompt": text, "session_id": session, "cwd": str(work), "hook_event_name": "UserPromptSubmit"}
    r = subprocess.run([sys.executable, str(WRAPPER)], input=json.dumps(payload), env=env, cwd=work,
                       capture_output=True, text=True, timeout=55)
    return (json.loads(r.stdout or "{}").get("additionalContext") or "")


def test_the_first_prompt_recalls_the_relevant_memory(plur_env):
    env, work = plur_env
    ctx = _prompt(env, work, "What currency do Meridian trades settle in?")
    assert "USDC" in ctx, f"session start recalled nothing relevant: {ctx[:300]!r}"
    assert "anchovies" not in ctx


def test_a_later_factual_question_recalls_its_own_memory(plur_env):
    env, work = plur_env
    _prompt(env, work, "What currency do Meridian trades settle in?")
    ctx = _prompt(env, work, "Which package manager should I use to install verity's dependencies?")
    assert "pnpm" in ctx, f"a later question in the same session got no relevant memory: {ctx[:300]!r}"
    assert "anchovies" not in ctx


@pytest.mark.production
def test_this_machine_wires_recall_into_every_session():
    events: dict[str, list[str]] = {}
    for p in (Path.home() / ".claude" / "settings.json", ROOT / ".claude" / "settings.json",
              ROOT / ".claude" / "settings.local.json"):
        try:
            hooks = json.loads(p.read_text()).get("hooks") or {}
        except (OSError, ValueError):
            continue
        for ev, entries in hooks.items():
            events.setdefault(ev, []).extend(h.get("command", "") for e in entries for h in e.get("hooks", []))
    problems = []
    if not any("plur_inject_wrapper" in c for c in events.get("UserPromptSubmit", [])):
        problems.append("no UserPromptSubmit hook runs plur_inject_wrapper")
    if not any("plur" in c for c in events.get("SessionStart", [])):
        problems.append("no SessionStart PLUR hook")
    sys.path.insert(0, str(LIB))
    import plur_cli
    try:
        plur_cli.command("hook-inject")
    except (OSError, ValueError) as exc:
        problems.append(f"the PLUR CLI the wrapper uses is not usable: {exc}")
    assert not problems, "; ".join(problems)
