"""SPC-3: Private and space-only context never appears in a public file, a
shared commit, or another space.

Kind: deterministic + production contract (local, read-only).

DIP-0002 layers: CLAUDE.base.md (public) + CLAUDE.space.md (space) +
CLAUDE.local.md (private) -> composed CLAUDE.md (never shared).

Deterministic (tmp space repo, local bare origin, NO .gitignore -- the state a
fresh or hand-made space is in; hooks disabled, so only the code is judged):
  * `context_merge.rebuild_context` refuses to write a composed file git would
    track while a private layer exists (the private text is written nowhere);
  * a sync never publishes the private layer or a composed file holding it:
    `ledger_transport.converge` (autosave `git add -A`) and
    `git_fleet_sync --execute`;
  * composing space A reads only A's layers, never B's private layer.

Production (@production, local): no repository on this machine (root and every
space) tracks a `*.local.md`, or a composed context file carrying a LOCAL
layer; every repository ignores `CLAUDE.local.md`.

Seeded failure: an autosave `git add -A` in a space whose .gitignore lacks
`*.local.md` / `CLAUDE.md` -- the private layer is committed and pushed.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

import context_merge

ROOT = Path(__file__).resolve().parents[3]
SECRET = "PRIVATE-ONLY: salary negotiation notes"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_ACTOR", "tester")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
    root = tmp_path / "Data"
    (root / ".datacore" / "registry").mkdir(parents=True)
    (root / ".datacore" / "registry" / "repositories.yaml").write_text(
        "repositories:\n  1-alpha:\n    category: knowledge\n")
    sp = root / "1-alpha"
    subprocess.run(["git", "init", "-q", "-b", "main", str(sp)], check=True, timeout=60)
    for k, v in (("user.email", "t@t"), ("user.name", "t"), ("core.hooksPath", str(hooks))):
        _git(sp, "config", k, v)
    _git(sp, "remote", "add", "origin", str(origin))
    (sp / "CLAUDE.base.md").write_text("# Alpha\n\nPublic guidance.\n")
    _git(sp, "add", "-A")
    _git(sp, "commit", "-qm", "seed")
    assert _git(sp, "push", "-q", "-u", "origin", "main").returncode == 0
    _git(sp, "remote", "set-head", "origin", "main")
    (sp / "CLAUDE.local.md").write_text(f"## Mine\n\n{SECRET}\n")
    return root, sp, origin


def _origin_leaks(origin: Path) -> list[str]:
    files = subprocess.run(["git", "-C", str(origin), "ls-tree", "-r", "--name-only", "main"],
                           capture_output=True, text=True, timeout=30).stdout.split()
    return [f for f in files if SECRET in subprocess.run(
        ["git", "-C", str(origin), "show", f"main:{f}"], capture_output=True, text=True, timeout=30).stdout]


def test_rebuild_refuses_a_composed_file_git_would_track(world):
    _, sp, _ = world
    ok, messages = context_merge.rebuild_context(sp, "CLAUDE")
    assert not ok, f"composed a private layer into a tracked file: {messages}"
    for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        p = sp / name
        assert not p.exists() or SECRET not in p.read_text(), f"{name} holds the private layer"


def _compose_by_hand(sp: Path) -> None:
    """A composed file already on disk (built where it was ignored, or by an older tool)."""
    content, _ = context_merge.merge_context(sp, "CLAUDE")
    (sp / "CLAUDE.md").write_text(content)


def test_converge_never_publishes_private_context(world):
    import ledger_transport as lt
    root, sp, origin = world
    _compose_by_hand(sp)
    lt.converge(sp, root=root)
    leaks = _origin_leaks(origin)
    assert not leaks, f"converge published the private layer in: {leaks}"


def test_fleet_sync_never_publishes_private_context(world, monkeypatch, capsys):
    import git_fleet_sync
    root, sp, origin = world
    _compose_by_hand(sp)
    monkeypatch.setattr(sys, "argv", ["git_fleet_sync.py", str(root), "--execute"])
    git_fleet_sync.main()
    capsys.readouterr()
    leaks = _origin_leaks(origin)
    assert not leaks, f"git_fleet_sync published the private layer in: {leaks}"


def test_composing_one_space_never_reads_another_spaces_private_layer(tmp_path):
    a, b = tmp_path / "1-alpha", tmp_path / "2-beta"
    for d in (a, b):
        d.mkdir()
        (d / "CLAUDE.base.md").write_text(f"# {d.name}\n")
    (b / "CLAUDE.local.md").write_text(f"{SECRET}\n")
    content, _ = context_merge.merge_context(a, "CLAUDE")
    assert SECRET not in content


@pytest.mark.production
def test_no_repository_here_tracks_private_context():
    repos = [ROOT, *sorted(p for p in ROOT.glob("[0-9]-*") if (p / ".git").exists())]
    problems = []
    for repo in repos:
        name = repo.name if repo != ROOT else "<root>"
        files = _git(repo, "ls-files")
        if files.returncode != 0:
            problems.append(f"{name}: could not list (could not check)")
            continue
        for f in files.stdout.splitlines():
            if f.endswith(".local.md"):
                problems.append(f"{name} tracks {f}")
            elif Path(f).name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
                head = (repo / f).read_text(errors="replace")[:4000] if (repo / f).is_file() else ""
                if context_merge.GENERATED_HEADER in head and "Layer: LOCAL" in head:
                    problems.append(f"{name} tracks composed {f} with a private layer")
        if _git(repo, "check-ignore", "-q", "CLAUDE.local.md").returncode != 0:
            problems.append(f"{name} does not ignore CLAUDE.local.md")
    assert not problems, "; ".join(problems)
