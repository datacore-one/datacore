"""LED-2: Nothing already in the history is ever changed or deleted; the
history only grows.

Kind: deterministic (tmp spaces, a local bare origin; public API only:
EventLog, read_events, verify_chain, ledger_cli verify, ledger_transport.converge).

Promise, as evals:
  * the writer only ever appends: every earlier file content is a byte prefix
    of the later one;
  * on the machine that wrote a log, `ledger_cli verify` fails when
      - its tail was cut off (truncation),
      - an old event was edited and the whole chain re-hashed so it is
        internally perfect (the audit A#2 rewrite),
      - the whole log file was deleted;
  * a sync (`converge`) never silently replaces history this machine already
    holds: when origin carries an edit of an existing event, converge refuses
    or the local log keeps its original lines.

Seeded failure: the audit A#2 rewrite -- edit event 0 and recompute hash/prev
for every later event. Only a witness outside the chain (a watermark of the
last hash, the git history) can see it; `verify_chain` alone cannot.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ledger.events import body_dict, compute_hash
from ledger.log import EventLog, read_events
from ledger.verify import verify_chain


def _cli_verify(space: Path, monkeypatch, capsys) -> tuple[int, str]:
    import ledger_cli
    monkeypatch.setattr(sys, "argv", ["ledger_cli.py", "verify", "--space", str(space)])
    code = 0
    try:
        ledger_cli.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out + out.err


def _space(root: Path, n: int = 4) -> tuple[Path, Path]:
    space = root / "9-fixture"
    log = EventLog(space, "miles", sign=False)
    for i in range(n):
        log.append("item.create", {"id": f"t{i}", "title": f"task {i}", "state": "NEXT"})
    return space, space / ".datacore" / "events" / "miles.jsonl"


def _rewrite_consistently(path: Path, index: int = 0) -> None:
    """Edit one event and re-chain everything after it: a perfect forged chain."""
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    lines[index]["payload"]["title"] = "rewritten"
    prev = lines[index]["prev"]
    for ev in lines[index:]:
        body = body_dict(ev["seq"], ev["hlc"], ev["actor"], ev["type"], ev["payload"], prev)
        ev["prev"] = prev
        ev["hash"] = compute_hash(body)
        ev["sig"] = ""
        prev = ev["hash"]
    from ledger.events import canonical_bytes
    path.write_text("".join(canonical_bytes({**ev}).decode() + "\n" for ev in lines))


@pytest.fixture(autouse=True)
def _unsigned(monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")


def test_the_writer_only_ever_appends(tmp_path):
    space = tmp_path / "9-fixture"
    log = EventLog(space, "miles", sign=False)
    path = space / ".datacore" / "events" / "miles.jsonl"
    before = b""
    for i in range(5):
        log.append("item.create", {"id": f"t{i}", "title": "x", "state": "NEXT"})
        now = path.read_bytes()
        assert now.startswith(before) and len(now) > len(before), "an append changed earlier bytes"
        before = now


def test_a_cut_tail_fails_verify(tmp_path, monkeypatch, capsys):
    space, path = _space(tmp_path)
    path.write_text("".join(path.read_text().splitlines(keepends=True)[:2]))
    assert verify_chain(path) == [], "precondition: a truncated chain is internally valid"
    code, out = _cli_verify(space, monkeypatch, capsys)
    assert code != 0, f"verify passed a log that lost its tail: {out}"


def test_a_rewritten_history_fails_verify(tmp_path, monkeypatch, capsys):
    space, path = _space(tmp_path)
    _rewrite_consistently(path, 0)
    assert "rewritten" in path.read_text()
    code, out = _cli_verify(space, monkeypatch, capsys)
    assert code != 0, f"verify passed an edited, re-chained history (audit A#2): {out.strip()}"


def test_a_deleted_log_fails_verify(tmp_path, monkeypatch, capsys):
    space, path = _space(tmp_path)
    path.unlink()
    code, out = _cli_verify(space, monkeypatch, capsys)
    assert code != 0, f"verify passed a space whose log this machine wrote was deleted: {out.strip()}"


# ── sync never replaces history already held ────────────────────────────────

def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _clone(origin: Path, dest: Path, hooks: Path) -> Path:
    subprocess.run(["git", "clone", "-q", str(origin), str(dest)], check=True, timeout=60)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks)),
                 ("commit.gpgsign", "false")):
        _git(dest, "config", k, v)
    return dest


def test_converge_never_silently_takes_an_edit_of_held_history(tmp_path, monkeypatch):
    import ledger_transport as lt
    monkeypatch.setattr(lt, "classify",
                        lambda space, root=None: lt.Result(True, "knowledge", {"entry": {"category": "knowledge"}}))
    hooks = tmp_path / "no-hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)

    a = tmp_path / "a" / "9-fixture"
    a.parent.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(a)], check=True, timeout=60)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks))):
        _git(a, "config", k, v)
    _git(a, "remote", "add", "origin", str(origin))
    log = EventLog(a, "miles", sign=False)
    for i in range(3):
        log.append("item.create", {"id": f"t{i}", "title": f"task {i}", "state": "NEXT"})
    _git(a, "add", "-A")
    _git(a, "commit", "-qm", "ledger")
    assert _git(a, "push", "-q", "origin", "main").returncode == 0

    b = _clone(origin, tmp_path / "b" / "9-fixture", hooks)
    held = (b / ".datacore" / "events" / "miles.jsonl").read_text()
    assert held.count("\n") == 3

    # Someone rewrites event 0 on machine A, bypassing every hook, and pushes.
    _rewrite_consistently(a / ".datacore" / "events" / "miles.jsonl", 0)
    _git(a, "commit", "-qam", "edit history")
    assert _git(a, "push", "-q", "origin", "main").returncode == 0

    result = lt.converge(b)
    after = (b / ".datacore" / "events" / "miles.jsonl").read_text()
    assert (not result.ok) or after.startswith(held), (
        f"converge reported {result.reason!r} and replaced history this machine already held "
        f"(event 0 now reads {json.loads(after.splitlines()[0])['payload']['title']!r})")
