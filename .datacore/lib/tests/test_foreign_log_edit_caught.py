"""Fleet sim fault F9, exactly as the simulator's stand-in performs it, is caught.

F9 (an agent hand-edits another writer's event log and commits it) showed "NOT
DETECTED" in the 2026-10-03 harsh week. The run's own record says why: the stand-in
logged "no other writer's log to hand-edit", so the edit never happened (the box's
0-personal held only its own log). This proves the ledger side independently: the
stand-in's mutation (copy the writer's last line, bump seq, change the title, append
with json.dumps, commit) is refused at commit by the central pre-commit dispatcher,
refused by the write gate on push, and reported by the chain verifier if it ever
lands.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from ledger.log import EventLog
from ledger.verify import verify_chain

LIB = Path(__file__).resolve().parents[1]
GATE = LIB / "hooks" / "ledger_write_gate.py"
HOOKS = LIB.parent / "githooks"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]


def _git(repo, *args, hooks="/dev/null"):
    return subprocess.run([*GIT, "-c", f"core.hooksPath={hooks}", *args], cwd=repo,
                          capture_output=True, text=True)


def _space(tmp_path):
    r = tmp_path / "0-fixture"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    log = EventLog(r, "miles", keys_dir=tmp_path / "keys", registry_path=tmp_path / "reg.yaml")
    log.append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    log.append("item.create", {"id": "t2", "title": "two", "state": "NEXT"})
    _git(r, "add", ".datacore/events/miles.jsonl")
    assert _git(r, "commit", "-q", "-m", "base").returncode == 0
    return r, log.path


def _f9_edit(path: Path) -> None:
    """sim/stand_in.py `_hand_edit`, verbatim in effect."""
    ev = json.loads(path.read_text().splitlines()[-1])
    ev["seq"] = int(ev.get("seq", 0)) + 1
    ev.setdefault("payload", {})["title"] = "hand-edited by an unattended agent"
    with path.open("a") as fh:
        fh.write(json.dumps(ev, sort_keys=True) + "\n")


def test_the_commit_is_refused_by_the_pre_commit_dispatcher(tmp_path):
    repo, path = _space(tmp_path)
    _f9_edit(path)
    _git(repo, "add", "--", ".datacore/events/miles.jsonl")
    r = _git(repo, "commit", "-qm", "tidy events", "--", ".datacore/events/miles.jsonl", hooks=HOOKS)
    assert r.returncode != 0, "the hand-edited log was committed"
    assert "miles.jsonl" in r.stdout + r.stderr, "the refusal does not name the log"


def test_the_write_gate_refuses_it_on_push(tmp_path):
    repo, path = _space(tmp_path)
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _f9_edit(path)
    _git(repo, "add", "--", ".datacore/events/miles.jsonl")
    assert _git(repo, "commit", "-qm", "tidy events").returncode == 0   # hooks bypassed
    r = subprocess.run([sys.executable, str(GATE), "--repo", str(repo), "--ranges", f"{base}..HEAD"],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "miles.jsonl" in r.stdout + r.stderr, r.stdout + r.stderr


def test_the_verifier_reports_it_if_it_lands(tmp_path):
    repo, path = _space(tmp_path)
    _f9_edit(path)
    errors = verify_chain(path, registry_path=tmp_path / "reg.yaml")
    assert any("line 3" in e for e in errors), errors
