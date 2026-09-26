"""LED-3: Only the ledger writer can add to the history; a hand-written or
impostor record is refused before it is saved or shared.

Seeded failure: 2026-09-25 an agent appended two events to 6-meridian's
miles.jsonl by hand (json.dumps with spaces, sig = hash, typed HLCs) and
committed and pushed them. Nothing on the write path looked at the bytes:
pre-commit has no ledger check, and pre-push only checks WHICH log a host
writes (audit A#6, D1). There is no `.datacore/lib/hooks/ledger_write_gate.py`.

Promise, as evals: for every staged (pre-commit) or pushed (pre-push) change to
`.datacore/events/*.jsonl`, the gate refuses a non-canonical line, a line whose
`actor` is not the log's writer, an edited old line, a line whose HLC is more
than ten minutes in the future, and a broken chain or hash -- and passes a
genuine EventLog append. The central pre-commit dispatcher runs it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from ledger.events import Event, body_dict, compute_hash, to_line
from ledger.log import EventLog

LIB = Path(__file__).resolve().parents[1]
GATE = LIB / "hooks" / "ledger_write_gate.py"
HOOKS = LIB.parent / "githooks"
GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid",
       "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]


def _git(repo: Path, *args: str, hooks: Path | None = None) -> subprocess.CompletedProcess:
    base = list(GIT)
    if hooks is not None:
        base[base.index("core.hooksPath=/dev/null")] = f"core.hooksPath={hooks}"
    return subprocess.run([*base, *args], cwd=repo, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "9-fixture"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    log = EventLog(r, "miles")
    log.append("item.create", {"id": "t1", "title": "one", "state": "NEXT"})
    log.append("item.create", {"id": "t2", "title": "two", "state": "NEXT"})
    _git(r, "add", ".datacore/events/miles.jsonl")
    assert _git(r, "commit", "-q", "-m", "base").returncode == 0
    return r


def _path(repo: Path) -> Path:
    return repo / ".datacore" / "events" / "miles.jsonl"


def _gate(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(GATE), "--repo", str(repo), *args],
                          capture_output=True, text=True)


def _refused(r: subprocess.CompletedProcess) -> bool:
    """Exit 1 is the gate's refusal, and it names the log (exit 2 = cannot run)."""
    return r.returncode == 1 and "miles.jsonl" in (r.stdout + r.stderr)


def _last(repo: Path) -> dict:
    return json.loads(_path(repo).read_text().splitlines()[-1])


def _next_body(repo: Path, **over) -> dict:
    last = _last(repo)
    body = body_dict(last["seq"] + 1, f"{int(last['hlc'].split('.')[0]) + 1}.0000.miles", "miles",
                     "item.create", {"id": "t3", "title": "three", "state": "NEXT"}, last["hash"])
    body.update(over)
    return body


def _write_canonical(repo: Path, body: dict, *, hash_: str | None = None) -> None:
    h = hash_ or compute_hash(body)
    with _path(repo).open("a") as f:
        f.write(to_line(Event(**body, hash=h, sig="")) + "\n")


def _stage(repo: Path) -> None:
    _git(repo, "add", ".datacore/events/miles.jsonl")


def test_a_genuine_eventlog_append_passes(repo):
    EventLog(repo, "miles").append("item.create", {"id": "t3", "title": "three", "state": "NEXT"})
    _stage(repo)
    r = _gate(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_hand_written_non_canonical_line_is_refused(repo):
    body = _next_body(repo)
    h = compute_hash(body)
    with _path(repo).open("a") as f:
        f.write(json.dumps({**body, "hash": h, "sig": h}) + "\n")
    _stage(repo)
    assert _refused(_gate(repo, "--staged"))


def test_an_actor_that_is_not_the_logs_writer_is_refused(repo):
    _write_canonical(repo, _next_body(repo, actor="winston"))
    _stage(repo)
    assert _refused(_gate(repo, "--staged"))


def test_an_edited_old_line_is_refused(repo):
    lines = _path(repo).read_text().splitlines()
    ev = json.loads(lines[0])
    ev["payload"]["title"] = "edited"
    ev["hash"] = compute_hash(body_dict(ev["seq"], ev["hlc"], ev["actor"], ev["type"], ev["payload"], ev["prev"]))
    lines[0] = to_line(Event(**ev))
    _path(repo).write_text("\n".join(lines) + "\n")
    _stage(repo)
    assert _refused(_gate(repo, "--staged"))


def test_a_deleted_line_is_refused(repo):
    lines = _path(repo).read_text().splitlines()
    _path(repo).write_text(lines[0] + "\n")
    _stage(repo)
    assert _refused(_gate(repo, "--staged"))


def test_a_future_hlc_is_refused(repo):
    future = int(time.time() * 1000) + 11 * 60 * 1000
    _write_canonical(repo, _next_body(repo, hlc=f"{future}.0000.miles"))
    _stage(repo)
    assert _refused(_gate(repo, "--staged"))


def test_a_near_future_hlc_within_tolerance_passes(repo):
    soon = int(time.time() * 1000) + 60 * 1000
    _write_canonical(repo, _next_body(repo, hlc=f"{soon}.0000.miles"))
    _stage(repo)
    r = _gate(repo, "--staged")
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("over", [{"seq": 5}, {"prev": "0" * 64}])
def test_a_broken_chain_is_refused(repo, over):
    _write_canonical(repo, _next_body(repo, **over))
    _stage(repo)
    assert _refused(_gate(repo, "--staged"))


def test_a_wrong_hash_is_refused(repo):
    _write_canonical(repo, _next_body(repo), hash_="0" * 64)
    _stage(repo)
    assert _refused(_gate(repo, "--staged"))


def test_a_pushed_range_with_a_hand_written_line_is_refused(repo):
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    body = _next_body(repo)
    h = compute_hash(body)
    with _path(repo).open("a") as f:
        f.write(json.dumps({**body, "hash": h, "sig": h}) + "\n")
    _stage(repo)
    _git(repo, "commit", "-q", "-m", "forged")
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _refused(_gate(repo, "--ranges", f"{base}..{tip}"))


def test_a_pushed_range_with_a_genuine_append_passes(repo):
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    EventLog(repo, "miles").append("item.create", {"id": "t3", "title": "three", "state": "NEXT"})
    _stage(repo)
    _git(repo, "commit", "-q", "-m", "genuine")
    tip = _git(repo, "rev-parse", "HEAD").stdout.strip()
    r = _gate(repo, "--ranges", f"{base}..{tip}")
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_pre_commit_dispatcher_refuses_a_hand_written_line(repo):
    body = _next_body(repo)
    h = compute_hash(body)
    with _path(repo).open("a") as f:
        f.write(json.dumps({**body, "hash": h, "sig": h}) + "\n")
    _stage(repo)
    r = _git(repo, "commit", "-q", "-m", "forged", hooks=HOOKS)
    assert r.returncode != 0, r.stdout + r.stderr
    assert "miles.jsonl" in (r.stdout + r.stderr)


def test_the_pre_push_hook_calls_the_gate():
    text = (HOOKS / "pre-push").read_text()
    assert "ledger_write_gate.py" in text
