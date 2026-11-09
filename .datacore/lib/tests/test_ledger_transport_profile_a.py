"""Ledger upgrade Phase 3 (audit C12): the transport serves a profile A install.

Profile A is one host or one shared git remote, humans only. Such an install
has none of this installation's tracked `registry/repositories.yaml`, and on a
single host its spaces have no remote at all.

  * NO REGISTRY ANYWHERE: a top-level space that declares itself by its marker
    (`.datacore/config.yaml`, which `ledger_cli.py init` writes) classifies as
    knowledge and converges with its remote. Before: "repository registry is
    unavailable or invalid" -- the marker was never consulted.
  * ONE HOST, NO REMOTE: converge says so as an explicit single-host mode and
    touches nothing (no autosave commit, no fetch). `sync` reports it as
    `local`. Before: "fetch failed (offline?)" on every cycle, forever.
  * Safety kept: a registry that EXISTS but is invalid still refuses everything,
    and a repo that is neither registered nor marked is still refused.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import ledger_transport as lt


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=60)


def _space(root: Path, name: str, *, origin: Path | None, hooks: Path, marker: bool = True) -> Path:
    sp = root / name
    subprocess.run(["git", "init", "-q", "-b", "main", str(sp)], check=True, timeout=60)
    for k, v in (("user.email", "t@example.invalid"), ("user.name", "t"), ("core.hooksPath", str(hooks)),
                 ("commit.gpgsign", "false")):
        _git(sp, "config", k, v)
    if marker:
        (sp / ".datacore").mkdir()
        (sp / ".datacore" / "config.yaml").write_text(f"space:\n  name: {name}\n  type: team\n")
    (sp / "README.md").write_text(f"# {name}\n")
    _git(sp, "add", "-A")
    _git(sp, "commit", "-qm", "seed")
    if origin is not None:
        subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)], check=True, timeout=60)
        _git(sp, "remote", "add", "origin", str(origin))
        assert _git(sp, "push", "-q", "-u", "origin", "main").returncode == 0
    return sp


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("DATACORE_LEDGER_SIGN", "0")
    monkeypatch.setenv("DATACORE_ACTOR", "alice")
    monkeypatch.setattr(lt, "SHIPPED_REGISTRY", tmp_path / "no-shipped-registry" / "repositories.yaml")
    root = tmp_path / "Data"
    root.mkdir()
    (tmp_path / "hooks").mkdir()
    return root


def test_a_marked_space_classifies_and_converges_with_no_registry_anywhere(root, tmp_path):
    sp = _space(root, "team", origin=tmp_path / "team.git", hooks=tmp_path / "hooks")
    assert not (root / ".datacore" / "registry").exists()
    cat = lt.classify(sp, root)
    assert cat.ok and cat.reason == "knowledge", f"a marked space is refused without a registry: {cat.reason}"

    (sp / "notes.md").write_text("a change to share\n")
    res = lt.converge(sp, root=root)
    assert res.ok, f"converge failed with no registry: {res.reason}"
    pushed = _git(tmp_path / "team.git", "show", "main:notes.md")
    assert pushed.returncode == 0 and "a change to share" in pushed.stdout, "the change did not reach the remote"


def test_sync_finds_marked_spaces_with_no_registry(root, tmp_path):
    _space(root, "team", origin=tmp_path / "team.git", hooks=tmp_path / "hooks")
    outcomes = lt.sync_outcomes(root)
    assert ("team", "knowledge", "clean") in outcomes, f"sync did not converge the marked space: {outcomes}"
    assert any(line.startswith("team:") for line in lt.status_lines(root)), \
        f"status does not list the marked space: {lt.status_lines(root)}"


def test_a_space_with_no_remote_is_single_host_and_untouched(root, tmp_path):
    sp = _space(root, "solo", origin=None, hooks=tmp_path / "hooks")
    (sp / "draft.md").write_text("not committed by anyone but me\n")
    head = _git(sp, "rev-parse", "HEAD").stdout
    res = lt.converge(sp, root=root)
    assert res.ok, f"a space with no remote is reported as a failure: {res.reason}"
    assert "single host" in res.reason and "no remote" in res.reason, f"the mode is not named: {res.reason}"
    assert _git(sp, "rev-parse", "HEAD").stdout == head, "converge committed in a single-host space"
    assert "draft.md" in _git(sp, "status", "--porcelain").stdout, "converge touched the working tree"
    assert ("solo", "knowledge", "local") in lt.sync_outcomes(root)


def test_an_invalid_registry_still_refuses_and_an_unmarked_repo_is_still_refused(root, tmp_path):
    plain = _space(root, "plain", origin=None, hooks=tmp_path / "hooks", marker=False)
    res = lt.classify(plain, root)
    assert not res.ok and "not in registry" in res.reason, f"an unmarked, unregistered repo was accepted: {res}"

    marked = _space(root, "team", origin=None, hooks=tmp_path / "hooks")
    reg = root / ".datacore" / "registry"
    reg.mkdir(parents=True)
    (reg / "repositories.yaml").write_text("repositories: [not, a, mapping]\n")
    res = lt.classify(marked, root)
    assert not res.ok and "invalid" in res.reason, f"an invalid registry did not refuse: {res}"



def test_a_first_converge_publishes_into_an_empty_remote(root, tmp_path):
    """A team's new remote has no branch yet. The first converge used to report
    'merge conflict -- human needed' (found running the profile A runbook,
    2026-10-04). An empty remote holds nothing to fork against or overwrite: the
    first converge publishes the space. A remote that HAS branches but not this
    one is not empty, and is never given a new default branch."""
    sp = _space(root, "team", origin=None, hooks=tmp_path / "hooks")
    bare = tmp_path / "empty.git"
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(bare)], check=True, timeout=60)
    _git(sp, "remote", "add", "origin", str(bare))
    res = lt.converge(sp, root=root)
    assert res.ok and "conflict" not in res.reason, f"an empty remote was not published: {res.reason}"
    assert "first publish" in res.reason, f"the first publish is not named: {res.reason}"
    assert _git(bare, "rev-parse", "--verify", "-q", "refs/heads/main").returncode == 0, "nothing reached the remote"
    assert lt.sync_repo(sp, quiet=True, root=root) == "clean"

    other = _space(root, "other", origin=None, hooks=tmp_path / "hooks")
    elsewhere = tmp_path / "elsewhere.git"
    # Its HEAD names `main`, which does not exist there: no default is learnt from it.
    subprocess.run(["git", "init", "-q", "--bare", "--initial-branch=main", str(elsewhere)], check=True, timeout=60)
    _git(other, "remote", "add", "origin", str(elsewhere))
    assert _git(other, "push", "-q", "origin", "main:trunk").returncode == 0
    res = lt.converge(other, root=root)
    assert not res.ok and "conflict" not in res.reason and "trunk" in res.reason, \
        f"a remote without this branch was not named as such: {res.reason}"
    assert _git(elsewhere, "rev-parse", "--verify", "-q", "refs/heads/main").returncode != 0, \
        "converge pushed a new default branch into a remote that already had branches"
