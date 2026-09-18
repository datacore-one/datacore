"""A space that is not its own repository cannot be compared, and must say so.

`git -C <dir>` answers from whatever repo ENCLOSES <dir>. So a space that is
merely a subdirectory of another checkout resolves
`origin/main:.datacore/events/<writer>.jsonl` to the ENCLOSING repo's log of
that name -- a different writer's entire history. Every shared (actor, seq)
then differs, and the detector reports the space as FORKED, which is the most
alarming verdict it has.

Observed on plur-claw, 2026-09-18: `~/Data/0-personal` has no `.git` of its
own, so all 8 of its `data` events were compared against the OpenClaw
workspace repo's `data.jsonl` and reported as 8 collisions. Nothing was forked.
"""
import json
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIB))

from ledger.fork import detect  # noqa: E402


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _event(actor, seq, title):
    return json.dumps({"actor": actor, "seq": seq, "hash": f"{actor}{seq}{title}"[:16],
                       "prev": "GENESIS", "hlc": f"100{seq}.0000.{actor}",
                       "type": "item.create", "payload": {"title": title}})


def _repo(path, log_lines):
    path.mkdir(parents=True, exist_ok=True)
    events = path / ".datacore" / "events"
    events.mkdir(parents=True, exist_ok=True)
    (events / "data.jsonl").write_text("\n".join(log_lines) + "\n")
    _git(path, "init", "-q", "-b", "main")
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"),
                 ("core.hooksPath", str(path / ".git" / "hooks"))):
        _git(path, "config", k, v)
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "seed")
    return path


def test_a_space_inside_another_checkout_is_not_judged(tmp_path):
    # The enclosing repo carries its OWN data.jsonl, with different events at
    # the same sequence numbers -- the shape that produced 8 phantom collisions.
    outer = _repo(tmp_path / "workspace",
                  [_event("data", n, f"outer {n}") for n in range(8)])
    _git(outer, "remote", "add", "origin", str(outer))
    _git(outer, "update-ref", "refs/remotes/origin/main", "HEAD")

    inner = outer / "0-personal" / ".datacore" / "events"
    inner.mkdir(parents=True)
    (inner / "data.jsonl").write_text(
        "\n".join(_event("data", n, f"inner {n}") for n in range(8)) + "\n")

    rep = detect(outer / "0-personal")

    assert rep.collisions == [], rep.collisions
    assert "not its own repository" in rep.reason, rep.reason
    assert not rep.clean or rep.reason, "a comparison that could not be made is not a pass"


def test_a_real_repository_is_still_compared(tmp_path):
    space = _repo(tmp_path / "5-plur", [_event("data", n, f"e{n}") for n in range(3)])
    _git(space, "remote", "add", "origin", str(space))
    _git(space, "update-ref", "refs/remotes/origin/main", "HEAD")

    rep = detect(space)

    assert rep.reason == "", rep.reason
    assert rep.clean and rep.checked == 3, (rep.clean, rep.checked)


def test_a_genuine_fork_is_still_reported(tmp_path):
    space = _repo(tmp_path / "5-plur", [_event("data", n, f"e{n}") for n in range(3)])
    _git(space, "remote", "add", "origin", str(space))
    _git(space, "update-ref", "refs/remotes/origin/main", "HEAD")
    # Same (actor, seq), different event: the thing the detector exists for.
    (space / ".datacore" / "events" / "data.jsonl").write_text(
        "\n".join(_event("data", n, f"CHANGED{n}") for n in range(3)) + "\n")

    rep = detect(space)

    assert rep.reason == ""
    assert not rep.clean and len(rep.collisions) == 3, rep.collisions
