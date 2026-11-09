"""SYN-9: One content conflict in a space never stops that whole space's sync. The
conflicting file waits for a person; everything else in the space keeps flowing.

Kind: deterministic. One space, a local bare origin and two clones: the executor
host (A) and the always-on host (B), the fleet week simulator's fault F8 shape. Both
commit a different version of one org file; A publishes first. Around that conflict
both hosts also do ordinary unrelated work: A adds a note, B adds a note and appends
a ledger event. B then syncs through each real path the always-on host runs on a
space, and syncs again an hour later after more unrelated work on both sides.

Paths (each is what a scheduled box job runs on a space):
  * converge        -- `ledger_transport.converge`: the phase-1 cycle and cos_sync;
  * claim loop      -- `ledger_claim_run.sh <space> <actor>`, exactly as cron runs it
                       (box-ledger-claim, every 15 minutes): pull, claim, converge;
  * fleet sync      -- `git_fleet_sync.main --execute --pull` (box-fleet-sync).

Graded after each sync, the first and the next:
  * nothing is left in progress in B's working tree (no merge, rebase, cherry-pick,
    no unmerged path), so no later cycle can refuse the space for it;
  * B's unrelated work (note, ledger event) is on origin; A's unrelated work is in B;
  * the conflicting file is named in what the sync reports -- on the first sync AND
    on the next one while it is still unresolved (it waits for a person; it is not
    mentioned once and forgotten);
  * neither version is lost or silently chosen: A's version is on origin's main, B's
    version is still reachable in B, and origin's copy holds no conflict markers.

Not required and not used: rebase, stash, reset or discarding either side.

Seeded failure (fleet week simulator 2026-09-30, fault F8): after a push conflict the
always-on host was left with a merge in progress, and for the rest of the week its
phase-1 cycle ("merge in progress -- finish or abort it by hand"), its fleet sync
("SKIP -- merge in progress"), its ledger claim and the phase-1 status check all
refused the space. Even without the stray merge, converge answers a content conflict
with "merge conflict -- human needed" and publishes nothing else from the space.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]

from ledger.log import EventLog  # noqa: E402

SPACE = "9-fixture"
SHARED = "org/shared.org"
V_A = "* Shared note\nversion written on the executor host\n"
V_B = "* Shared note\nversion written on the always-on host\n"
MARKERS = ("<<<<<<<", ">>>>>>>")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def world(tmp_path, monkeypatch):
    import actor_identity
    root = tmp_path / "Data"
    reg = root / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "principals.yaml").write_text("principals:\n  p:\n    kind: agent\n    writes_as: [tester]\n"
                                         "  q:\n    kind: agent\n    writes_as: [other]\n")
    (reg / "repositories.yaml").write_text(f"repositories:\n  {SPACE}:\n    category: knowledge\n")
    (root / ".datacore" / "lib").symlink_to(LIB)          # what the claim loop runs from ROOT
    monkeypatch.setattr(actor_identity, "PRINCIPALS", reg / "principals.yaml")
    monkeypatch.setenv("DATACORE_ROOT", str(root))
    monkeypatch.setenv("DATACORE_ACTOR", "tester")
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    (tmp_path / "gitconfig").write_text("[init]\n\tdefaultBranch = main\n")
    hooks = tmp_path / "hooks"
    hooks.mkdir()

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    b = root / SPACE                                        # the always-on host's checkout
    a = tmp_path / "executor" / SPACE                       # the executor host's checkout
    a.parent.mkdir()
    subprocess.run(["git", "clone", "-q", str(origin), str(b)], check=True, timeout=60)
    for repo, who in ((b, "b"),):
        for k, v in (("user.email", f"{who}@t"), ("user.name", who), ("core.hooksPath", str(hooks))):
            _git(repo, "config", k, v)
    (b / "org").mkdir()
    (b / "notes").mkdir()
    (b / SHARED).write_text("* Shared note\nfirst version\n")
    (b / "notes" / "seed.md").write_text("seed\n")
    (b / ".gitignore").write_text(".datacore/state/*\n")   # as every real space has it
    _git(b, "add", "-A")
    _git(b, "commit", "-qm", "seed")
    assert _git(b, "push", "-q", "-u", "origin", "HEAD:main").returncode == 0
    _git(b, "remote", "set-head", "origin", "main")
    subprocess.run(["git", "clone", "-q", str(origin), str(a)], check=True, timeout=60)
    for k, v in (("user.email", "a@t"), ("user.name", "a"), ("core.hooksPath", str(hooks))):
        _git(a, "config", k, v)

    # F8: both hosts commit a different version of one file; A publishes first.
    (a / SHARED).write_text(V_A)
    (a / "notes" / "from-executor.md").write_text("unrelated work on the executor host\n")
    _git(a, "add", "-A")
    _git(a, "commit", "-qm", "executor: shared note + its own note")
    assert _git(a, "push", "-q", "origin", "main").returncode == 0
    (b / SHARED).write_text(V_B)
    _git(b, "commit", "-qm", "always-on: shared note", "--", SHARED)
    # B's unrelated work, some committed by its jobs, some not yet
    (b / "notes" / "from-always-on.md").write_text("unrelated work on the always-on host\n")
    EventLog(b, "tester", sign=False).append("item.create", {"id": "t1", "title": "t", "state": "NEXT"})
    return {"root": root, "a": a, "b": b, "origin": origin, "tmp": tmp_path}


# ------------------------------------------------------------------------ the sync paths

def _converge(w: dict, monkeypatch, capsys) -> str:
    import ledger_transport as lt
    res = lt.converge(w["b"], root=w["root"])
    return f"{res.reason} {res.context}"


def _claim_loop(w: dict, monkeypatch, capsys) -> str:
    state = w["tmp"] / "state"
    state.mkdir(mode=0o700, exist_ok=True)                 # private, as a host creates it
    state.chmod(0o700)
    env = {**os.environ, "HOME": str(w["tmp"] / "home"), "DATACORE_STATE": str(state)}
    (w["tmp"] / "home").mkdir(exist_ok=True)
    r = subprocess.run(["bash", str(LIB / "ledger_claim_run.sh"), str(w["b"]), "tester"],
                       env=env, capture_output=True, text=True, timeout=300)
    log = state / "ledger-claim.log"
    return (log.read_text() if log.exists() else "") + r.stdout + r.stderr


def _fleet_sync(w: dict, monkeypatch, capsys) -> str:
    import git_fleet_sync
    capsys.readouterr()
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(w["root"]), "--execute", "--pull"])
    try:
        git_fleet_sync.main()
    except SystemExit:
        pass
    return capsys.readouterr().out


PATHS = {"converge": _converge, "claim loop": _claim_loop, "fleet sync": _fleet_sync}


# ----------------------------------------------------------------------------- checks

def _in_progress(repo: Path) -> list[str]:
    found = []
    gitdir = Path(_git(repo, "rev-parse", "--absolute-git-dir").stdout.strip())
    for marker, name in (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"),
                         ("rebase-apply", "a rebase"), ("CHERRY_PICK_HEAD", "a cherry-pick"),
                         ("REVERT_HEAD", "a revert")):
        if (gitdir / marker).exists():
            found.append(name)
    if _git(repo, "ls-files", "--unmerged").stdout.strip():
        found.append("unmerged paths")
    return found


def _on_origin(origin: Path, path: str, text: str) -> bool:
    r = subprocess.run(["git", "-C", str(origin), "show", f"main:{path}"], capture_output=True, text=True, timeout=30)
    return r.returncode == 0 and text in r.stdout


def _reachable_in(repo: Path, path: str, text: str) -> bool:
    """Some commit reachable from a ref in `repo`, or its working tree, holds `text` in `path`."""
    wt = repo / path
    if wt.is_file() and text in wt.read_text():
        return True
    revs = _git(repo, "rev-list", "--all", "--", path).stdout.split()
    return any(text in _git(repo, "show", f"{rev}:{path}").stdout for rev in revs)


def _flowing(w: dict, report: str, *, b_note: str, event_id: str, a_note: str) -> list[str]:
    b, origin = w["b"], w["origin"]
    problems = []
    busy = _in_progress(b)
    if busy:
        problems.append(f"left {', '.join(busy)} in progress in the space")
    if not _on_origin(origin, b_note, "unrelated"):
        problems.append(f"this host's unrelated work ({b_note}) did not reach origin")
    if not _on_origin(origin, ".datacore/events/tester.jsonl", f'"id":"{event_id}"'):
        problems.append("this host's ledger event did not reach origin")
    if not (b / a_note).exists():
        problems.append(f"the other host's unrelated work ({a_note}) did not reach this host")
    if SHARED not in report:
        problems.append(f"the conflicting file ({SHARED}) is not named in what the sync reports")
    if not _on_origin(origin, SHARED, "") or not _reachable_in(origin, SHARED, "executor host"):
        problems.append("the first-published version was lost from origin")
    if not _reachable_in(b, SHARED, "always-on host"):
        problems.append("this host's version was discarded")
    shown = subprocess.run(["git", "-C", str(origin), "show", f"main:{SHARED}"],
                           capture_output=True, text=True, timeout=30).stdout
    if any(m in shown for m in MARKERS):
        problems.append("conflict markers were published to origin")
    return problems


@pytest.mark.parametrize("path", sorted(PATHS))
def test_one_conflict_waits_while_the_rest_of_the_space_flows(world, monkeypatch, capsys, path):
    sync = PATHS[path]
    report = sync(world, monkeypatch, capsys)
    first = _flowing(world, report, b_note="notes/from-always-on.md", event_id="t1",
                     a_note="notes/from-executor.md")
    assert not first, f"{path}, first sync after the conflict: " + "; ".join(first) + \
        f" -- report: {report.strip()[-300:]!r}"

    # An hour later: more unrelated work on both hosts, the conflict still unresolved.
    a, b = world["a"], world["b"]
    _git(a, "pull", "-q", "--no-rebase", "origin", "main")
    (a / "notes" / "later-executor.md").write_text("later unrelated work on the executor host\n")
    _git(a, "add", "--", "notes/later-executor.md")
    _git(a, "commit", "-qm", "executor: later note", "--", "notes/later-executor.md")
    assert _git(a, "push", "-q", "origin", "main").returncode == 0
    (b / "notes" / "later-always-on.md").write_text("later unrelated work on the always-on host\n")
    EventLog(b, "tester", sign=False).append("item.create", {"id": "t2", "title": "later", "state": "NEXT"})
    report = sync(world, monkeypatch, capsys)
    later = _flowing(world, report, b_note="notes/later-always-on.md", event_id="t2",
                     a_note="notes/later-executor.md")
    assert not later, f"{path}, the next sync: " + "; ".join(later) + \
        f" -- report: {report.strip()[-300:]!r}"
