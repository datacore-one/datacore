"""T1 (ledger-upgrade Phase 1, audit A#6, D1, D5): a hand-written ledger line
-- not the canonical bytes EventLog writes, or an `actor` that is not the log
file's writer -- is refused at commit AND at push, by the hooks this install
actually runs (`.datacore/githooks/pre-commit`, `.datacore/githooks/pre-push`),
end to end through git. A genuine EventLog append goes through both.

The gate's individual checks (append-only, chain, hash, HLC) are pinned
directly against `ledger_write_gate.py` by promise LED-3
(test_promise_LED3_write_gate.py). LED-3 drives the pre-commit dispatcher
through git but only reads the pre-push hook's text. This eval runs a real
`git push` to a bare remote, so a pre-push that stops calling the gate -- or
calls it on the wrong range -- turns it red.

Seeded failure: remove the `"$GATE_PY" "$LEDGER_GATE" --ranges ...` line from
githooks/pre-push (or the `python3 "$ledger_gate" ... --staged` line from
githooks/pre-commit). The 2026-09-25 forgery was committed and pushed through
exactly that gap: nothing on the write path looked at the bytes.

Runs in a disposable repository under tmp; never touches a live space.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from ledger.events import body_dict, compute_hash
from ledger.log import EventLog

LIB = Path(__file__).resolve().parents[2]
HOOKS = LIB.parent / "githooks"


def _writer() -> str:
    """The log this machine owns. The pre-push ownership guard (a separate
    check) refuses any other log, so the eval writes as this host's actor."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("log_ownership_guard_t1", LIB / "hooks" / "log_ownership_guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.actors()[0]


WRITER = _writer()
REL = f".datacore/events/{WRITER}.jsonl"
IMPOSTOR = "someone-else" if WRITER != "someone-else" else "another"


def _env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
                "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"})
    env.pop("SKIP_PRE_PUSH", None)
    env.pop("SKIP_FORMAL", None)
    return env


def _git(repo: Path, env: dict, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={HOOKS}", *args],
                          cwd=repo, env=env, capture_output=True, text=True, timeout=300)


@pytest.fixture
def world(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = _env(home)
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True,
                   capture_output=True, env=env)
    repo = tmp_path / "work" / "9-fixture"
    repo.mkdir(parents=True)
    assert _git(repo, env, "init", "-q", "-b", "main").returncode == 0
    log = EventLog(repo, WRITER)
    log.append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    log.append("item.create", {"id": "t2", "title": "two", "state": "NEXT"})
    _git(repo, env, "add", REL)
    base = _git(repo, env, "commit", "-q", "-m", "base")
    assert base.returncode == 0, f"SETUP: a genuine base commit was refused:\n{base.stderr}"
    _git(repo, env, "remote", "add", "origin", str(remote))
    pushed = _git(repo, env, "push", "-q", "origin", "main")
    assert pushed.returncode == 0, f"SETUP: pushing a genuine base was refused:\n{pushed.stderr}"
    return repo, env


def _hand_written(repo: Path, *, actor: str | None = None, spaced: bool = True) -> None:
    """Append the way the 2026-09-25 heartbeat did: json.dumps, sig = hash."""
    actor = actor or WRITER
    path = repo / REL
    last = json.loads(path.read_text().splitlines()[-1])
    body = body_dict(last["seq"] + 1, f"{int(last['hlc'].split('.')[0]) + 1000}.0000.{actor}", actor,
                     "metric.attest", {"metric": "cadence.run", "result": "ok"}, last["hash"])
    h = compute_hash(body)
    line = json.dumps({**body, "hash": h, "sig": h}) if spaced else \
        json.dumps({**body, "hash": h, "sig": ""}, sort_keys=True, separators=(",", ":"))
    with path.open("a") as f:
        f.write(line + "\n")


def _refused_by_gate(r: subprocess.CompletedProcess, why: str) -> bool:
    out = r.stdout + r.stderr
    return r.returncode != 0 and "ledger-write-gate: REFUSED" in out and why in out


def test_a_genuine_append_commits_and_pushes(world):
    repo, env = world
    EventLog(repo, WRITER).append("item.create", {"id": "t3", "title": "three", "state": "NEXT"})
    _git(repo, env, "add", REL)
    c = _git(repo, env, "commit", "-q", "-m", "genuine")
    assert c.returncode == 0, f"a genuine EventLog append must commit; the hooks said:\n{c.stderr}"
    p = _git(repo, env, "push", "-q", "origin", "main")
    assert p.returncode == 0, f"a genuine EventLog append must push; the hooks said:\n{p.stderr}"


def test_a_hand_written_line_is_refused_at_commit(world):
    repo, env = world
    _hand_written(repo)
    _git(repo, env, "add", REL)
    c = _git(repo, env, "commit", "-q", "-m", "hand-written")
    assert _refused_by_gate(c, "not canonical bytes"), (
        "expected: the real pre-commit hook refuses a hand-written (non-canonical) ledger line, "
        f"naming the ledger write gate.\nseen: exit {c.returncode}\n{c.stdout}{c.stderr}")


def test_an_impostor_actor_is_refused_at_commit(world):
    repo, env = world
    _hand_written(repo, actor=IMPOSTOR, spaced=False)
    _git(repo, env, "add", REL)
    c = _git(repo, env, "commit", "-q", "-m", "impostor")
    assert _refused_by_gate(c, "is not this log's writer"), (
        "expected: the real pre-commit hook refuses a line whose actor is not the log file's writer."
        f"\nseen: exit {c.returncode}\n{c.stdout}{c.stderr}")


def test_a_hand_written_line_is_refused_at_push(world):
    repo, env = world
    _hand_written(repo)
    _git(repo, env, "add", REL)
    # --no-verify: the line got into a commit some other way (another machine,
    # a hook-less clone, an agent that skipped hooks). Push must still refuse.
    c = _git(repo, env, "commit", "-q", "--no-verify", "-m", "hand-written, hooks skipped")
    assert c.returncode == 0, f"SETUP: --no-verify commit failed:\n{c.stderr}"
    p = _git(repo, env, "push", "-q", "origin", "main")
    assert _refused_by_gate(p, "not canonical bytes"), (
        "expected: the real pre-push hook refuses a pushed range carrying a hand-written ledger line."
        f"\nseen: exit {p.returncode}\n{p.stdout}{p.stderr}")
    remote_tip = subprocess.run(["git", "-C", str(repo), "ls-remote", "origin", "main"], env=env,
                                capture_output=True, text=True).stdout.split()[0]
    local_tip = _git(repo, env, "rev-parse", "HEAD").stdout.strip()
    assert remote_tip != local_tip, "the refused commit reached the remote anyway"


def test_an_impostor_actor_is_refused_at_push(world):
    repo, env = world
    _hand_written(repo, actor=IMPOSTOR, spaced=False)
    _git(repo, env, "add", REL)
    assert _git(repo, env, "commit", "-q", "--no-verify", "-m", "impostor").returncode == 0
    p = _git(repo, env, "push", "-q", "origin", "main")
    assert _refused_by_gate(p, "is not this log's writer"), (
        "expected: the real pre-push hook refuses a pushed line whose actor is not the log's writer."
        f"\nseen: exit {p.returncode}\n{p.stdout}{p.stderr}")
