"""SPC-7: A correction I give once is remembered and applied in later sessions
on any machine.

Kind: deterministic + production contract (read-only ssh). Agent part pending.

Deterministic: a correction stored the way a session stores it (`plur learn`,
what plur_learn does) in a throwaway PLUR store is recalled by the real
UserPromptSubmit hook (`plur_inject_wrapper.py`) in a LATER session (new
session id) for a prompt it applies to -- and the superseded advice is not.

Production (@production): the mac's PLUR store (~/.plur, a git repo synced to
the shared plur-engrams remote) as it stood a day ago must be in the store of
the owner's other machines (box, nightshift):
`git merge-base --is-ancestor <mac commit older than 24 h> HEAD` there.
The agents' own machines (hermes, plur-claw) must NOT hold it: the owner's
store carries private, personal, trading and client-project memory, and those
machines get only shared scopes (owner decision, 2026-09-29; owner-approved
revision of this eval, which previously required every machine to hold it).

Agent part pending: that a session calls plur_learn when corrected needs the
agent harness to wire PLUR into its child (see SPC-6).

Seeded failure: a correction learned on the mac that never leaves it -- a
host whose ~/.plur stopped syncing (or has none) never applies it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
WRAPPER = LIB / "hooks" / "plur_inject_wrapper.py"
HOSTS = {"box": "winston", "nightshift": "nightshift"}              # the owner's machines: full memory
AGENT_HOSTS = {"hermes": "hermes", "plur-claw": "plur-claw"}       # agents' machines: shared scopes only


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
    return env, work, shim


def _prompt(env, work, text, session):
    payload = {"prompt": text, "session_id": session, "cwd": str(work), "hook_event_name": "UserPromptSubmit"}
    r = subprocess.run([sys.executable, str(WRAPPER)], input=json.dumps(payload), env=env, cwd=work,
                       capture_output=True, text=True, timeout=55)
    return json.loads(r.stdout or "{}").get("additionalContext") or ""


def test_a_correction_is_applied_in_a_later_session(plur_env):
    env, work, shim = plur_env
    old = subprocess.run([str(shim), "--json", "--quiet", "learn",
                          "Deploy the website with npm run deploy"], env=env, cwd=work,
                         capture_output=True, text=True, timeout=30)
    old_id = json.loads(old.stdout)["id"]
    # Session 1: the user corrects it; the session records the correction.
    new = subprocess.run([str(shim), "--json", "--quiet", "learn",
                          "Deploy the website with the deploy.sh script, never npm run deploy",
                          "--supersedes", old_id], env=env, cwd=work, capture_output=True, text=True, timeout=30)
    assert new.returncode == 0, new.stderr
    # Session 2, later: a related prompt.
    ctx = _prompt(env, work, "How do I deploy the website?", "session-2")
    assert "deploy.sh" in ctx, f"the correction was not recalled in a later session: {ctx[:300]!r}"
    assert old_id not in ctx, f"the superseded advice {old_id} is still injected beside the correction"


def _ssh(host, cmd):
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", host, cmd],
                          capture_output=True, text=True, timeout=40)


def _mac_commit_a_day_old():
    store = Path.home() / ".plur"
    r = subprocess.run(["git", "-C", str(store), "log", "--format=%H %ct", "-200"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, "the mac's PLUR store is not a git repository (could not check)"
    cutoff = time.time() - 86400
    commit = next((c for c, t in (l.split() for l in r.stdout.splitlines()) if int(t) < cutoff), None)
    assert commit, "no mac PLUR commit older than a day (could not check)"
    return commit


def _holds(hosts, commit):
    cmd = (f"for d in ~/.plur ~gregor/.plur; do [ -d \"$d/.git\" ] && "
           f"{{ git -C \"$d\" merge-base --is-ancestor {commit} HEAD 2>/dev/null && echo HAS || echo LACKS; exit 0; }}; "
           f"done; echo NOSTORE")
    with ThreadPoolExecutor(len(hosts)) as pool:
        outs = dict(zip(hosts, pool.map(lambda h: _ssh(h, cmd), hosts.values())))
    return {name: (None if res.returncode != 0 and not res.stdout.strip()
                   else (res.stdout.strip().splitlines() or ["?"])[-1], res.returncode)
            for name, res in outs.items()}


@pytest.mark.production
def test_every_owner_machine_holds_the_macs_memory_from_a_day_ago():
    commit = _mac_commit_a_day_old()
    problems = []
    for name, (word, rc) in _holds(HOSTS, commit).items():
        if word is None:
            problems.append(f"{name}: could not check (ssh rc={rc})")
        elif word != "HAS":
            problems.append(f"{name}: {'no synced PLUR store' if word == 'NOSTORE' else 'lacks'} "
                            f"the mac's memory as of {commit[:8]}")
    assert not problems, "; ".join(problems)


@pytest.mark.production
def test_no_agent_machine_holds_the_owners_memory():
    """The owner's full store stays on the owner's machines; an agent machine
    that holds the mac's commit holds private and client-project memory."""
    commit = _mac_commit_a_day_old()
    problems = []
    for name, (word, rc) in _holds(AGENT_HOSTS, commit).items():
        if word is None:
            problems.append(f"{name}: could not check (ssh rc={rc})")
        elif word == "HAS":
            problems.append(f"{name}: holds the owner's full memory as of {commit[:8]}")
    assert not problems, "; ".join(problems)
