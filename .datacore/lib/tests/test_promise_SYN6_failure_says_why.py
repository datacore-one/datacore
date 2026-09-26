"""SYN-6: When sync fails it says why in plain terms (offline, access denied,
conflict) and never reports success.

Kind: deterministic, end to end through real git. Each space holds local work
that must be sent; then one of three causes blocks the sync:
  * offline -- origin is `git@example.invalid:...` and ssh (GIT_SSH_COMMAND,
    a stub) answers the way an unreachable host does ("connect ... timed out");
  * denied  -- the same stub answers "Permission denied (publickey)";
  * conflict -- a real bare origin that changed the same line.

Entry points, each asked what happened:
  * converge  -- `ledger_transport.sync_repo` (the operator word it prints)
  * fleet     -- `git_fleet_sync.main --execute --pull` (output + exit code:
                 exit 0 is how the timer reports success)
  * cos_sync  -- the box's chief-of-staff `cos_sync.sh` (its per-space line)

Promise: the words say the cause (offline / denied|access|auth / conflict),
and nothing says success (clean, synced, PUSHED, exit 0).

Seeded failure: the pre-OI-10 fleet sweep, where a pull that failed for any
reason printed "PULL CONFLICT" and a failed push left the run at exit 0.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
COS_SYNC = LIB.parent / "modules" / "chief-of-staff" / "server" / "lib" / "cos_sync.sh"
STDERR = {
    "offline": "ssh: connect to host example.invalid port 22: Operation timed out",
    "denied": "git@example.invalid: Permission denied (publickey).",
}
WORDS = {"offline": r"offline", "denied": r"denied|access|auth", "conflict": r"conflict"}
SUCCESS = re.compile(r"\bclean\b|synced|PUSHED|converged\b(?! but)")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def space(tmp_path, monkeypatch, request):
    cause = request.param
    monkeypatch.setenv("DATACORE_ACTOR", "tester")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    root = tmp_path / "Data"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "registry" / "repositories.yaml").write_text(
        "repositories:\n  9-fixture:\n    category: knowledge\n")
    sp = root / "9-fixture"
    subprocess.run(["git", "clone", "-q", str(origin), str(sp)], check=True, timeout=60)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks))):
        _git(sp, "config", k, v)
    (sp / "note.md").write_text("one\ntwo\nthree\n")
    _git(sp, "add", "-A")
    _git(sp, "commit", "-qm", "seed")
    assert _git(sp, "push", "-q", "-u", "origin", "HEAD:main").returncode == 0
    _git(sp, "remote", "set-head", "origin", "main")
    if cause == "conflict":
        other = tmp_path / "other"
        subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, timeout=60)
        for k, v in (("user.email", "o@t"), ("user.name", "o"), ("core.hooksPath", str(hooks))):
            _git(other, "config", k, v)
        (other / "note.md").write_text("one\nTWO elsewhere\nthree\n")
        _git(other, "commit", "-qam", "elsewhere")
        assert _git(other, "push", "-q", "origin", "main").returncode == 0
        (sp / "note.md").write_text("one\nTWO here\nthree\n")
    else:
        stub = tmp_path / "ssh-stub"
        stub.write_text(f"#!/bin/sh\necho '{STDERR[cause]}' >&2\nexit 255\n")
        stub.chmod(0o755)
        monkeypatch.setenv("GIT_SSH_COMMAND", str(stub))
        _git(sp, "remote", "set-url", "origin", "git@example.invalid:owner/9-fixture.git")
        (sp / "work.md").write_text("local work to send\n")
    return cause, root, sp, tmp_path


CAUSES = ["offline", "denied", "conflict"]


def _check(entry: str, cause: str, words: str, failed: bool) -> None:
    assert failed, f"{entry} reported success on {cause}: {words!r}"
    assert re.search(WORDS[cause], words, re.I), f"{entry} did not say {cause!r}: {words!r}"
    assert not SUCCESS.search(words), f"{entry} used a success word on {cause}: {words!r}"


@pytest.mark.parametrize("space", CAUSES, indirect=True)
def test_converge_names_the_cause(space, capsys):
    import ledger_transport as lt
    cause, root, sp, _ = space
    outcome = lt.sync_repo(sp, root=root)
    printed = capsys.readouterr().out
    _check("converge", cause, printed, outcome != "clean")


@pytest.mark.parametrize("space", CAUSES, indirect=True)
def test_fleet_sync_names_the_cause(space, monkeypatch, capsys):
    import git_fleet_sync
    cause, root, sp, _ = space
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(root), "--execute", "--pull"])
    code = git_fleet_sync.main()
    out = capsys.readouterr().out
    _check("git_fleet_sync", cause, out, code != 0)


@pytest.mark.parametrize("space", CAUSES, indirect=True)
def test_cos_sync_names_the_cause(space):
    cause, root, sp, tmp = space
    lib = root / ".datacore" / "lib"
    lib.mkdir(parents=True, exist_ok=True)
    os.symlink(LIB / "ledger_transport.py", lib / "ledger_transport.py")
    (lib / "cos_alert.sh").write_text("#!/bin/sh\necho \"ALERT $*\"\n")
    (lib / "cos_alert.sh").chmod(0o755)
    home = tmp / "home"
    (home / ".datacore" / "cos").mkdir(parents=True)
    env = {**os.environ, "HOME": str(home), "DATACORE_HOME": str(root), "DATACORE_ROOT": str(root),
           "PATH": f"{Path(sys.executable).parent}:{os.environ.get('PATH', '')}",
           "GIT_CONFIG_GLOBAL": "/dev/null"}
    r = subprocess.run(["bash", str(COS_SYNC)], env=env, capture_output=True, text=True, timeout=60)
    line = next((l for l in r.stdout.splitlines() if "9-fixture" in l), "")
    _check("cos_sync", cause, line, bool(line) and "synced clean" not in line)
