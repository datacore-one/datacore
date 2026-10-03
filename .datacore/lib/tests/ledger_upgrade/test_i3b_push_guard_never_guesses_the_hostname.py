"""I3b (deterministic): the push guard a profile A install wires never guesses the hostname.

Ledger upgrade Phase 3, found running the profile A runbook on a clean machine
(2026-10-04). `ledger_cli.py init` wires Datacore's git hooks into a shared
space; their pre-push ownership guard (lib/hooks/log_ownership_guard.py)
resolves the writer as registry -> DATACORE_ACTOR -> HOSTNAME and never reads
~/.datacore/identity.env, the machine's declaration (DIP-0044) that init
writes. On a team's machine (no fleet registry) the first push of a person's
OWN log is refused as "<hostname> modified another actor's event log" -- and on
the owner's Mac the hostname guessed is a fleet name (I3's class of defect, in
a file I3's Python closure does not reach).

RED until the owner changes the guard: the config-protection hook refuses an
implementer's edit to it (2026-10-04, "a do-nothing stub" -- a false positive:
the proposed change adds one lookup before the hostname fallback; the patch is
in the Phase 3 report). Another writer's log must stay refused.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _repo(path: Path) -> Path:
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, timeout=60)
    return path


def test_the_push_guard_knows_the_writer_init_declared(tmp_path):
    """A team's machine has no fleet registry. The pre-push ownership guard used to
    fall back from DATACORE_ACTOR straight to the hostname, ignoring the identity
    file init writes, so alice's first push of her OWN log was refused as the
    hostname "modifying another actor's event log" (profile A runbook, 2026-10-04).
    It reads the declared identity before guessing; another writer's log is still refused."""
    import os
    import sys
    guard = Path(__file__).resolve().parents[2] / "hooks" / "log_ownership_guard.py"
    home = tmp_path / "home"
    (home / ".datacore").mkdir(parents=True)
    ident = home / ".datacore" / "identity.env"
    ident.write_text("DATACORE_ACTOR=alice\n")
    repo = _repo(tmp_path / "team")
    for k, v in (("user.email", "a@example.invalid"), ("user.name", "a"), ("commit.gpgsign", "false")):
        _git(repo, "config", k, v)
    (repo / "README.md").write_text("x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    _git(repo, "branch", "base-ref")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATACORE_", "GIT_AUTHOR_", "GIT_COMMITTER_"))}
    env.update(HOME=str(home), DATACORE_ROOT=str(tmp_path / "no-registry"), DATACORE_IDENTITY_FILE=str(ident))

    (repo / ".datacore" / "events").mkdir(parents=True)
    (repo / ".datacore" / "events" / "alice.jsonl").write_text('{"x":1}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alice's own log")
    own = subprocess.run([sys.executable, str(guard), "base-ref..HEAD"], cwd=repo, env=env,
                         capture_output=True, text=True, timeout=60)
    assert own.returncode == 0, f"the guard refused alice's own log:\n{own.stdout}{own.stderr}"

    (repo / ".datacore" / "events" / "bob.jsonl").write_text('{"forged":1}\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "alice writes bob's log")
    other = subprocess.run([sys.executable, str(guard), "base-ref..HEAD"], cwd=repo, env=env,
                           capture_output=True, text=True, timeout=60)
    assert other.returncode == 1 and "bob.jsonl" in other.stdout + other.stderr, \
        f"the guard let alice write bob's log:\n{other.stdout}{other.stderr}"
