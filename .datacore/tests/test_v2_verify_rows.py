"""datacore#34: repo topology, version parity, and the trust-label rule as
verifier rows. Each row is exercised against a fixture root, never the real
installation, so the tests pass on a machine with any number of repos."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(LIB))
import v2_verify as vv  # noqa: E402


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    e = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
         "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x", **(env or {})}
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, env=e, check=True).stdout


def _repo_with_remote(root: Path, name: str, remote_name: str, *, branch: str = "main") -> Path:
    bare = root / "remotes" / f"{remote_name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", "-b", branch, str(bare)], check=True)
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(repo)], check=True)
    (repo / "README").write_text("x\n")
    _git(repo, "add", "README")
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", branch)
    _git(repo, "remote", "set-head", "origin", branch)
    return repo


def _registry(root: Path, entries: dict) -> None:
    import yaml
    reg = root / ".datacore" / "registry"
    reg.mkdir(parents=True, exist_ok=True)
    (reg / "repositories.yaml").write_text(yaml.safe_dump({"repositories": entries}))


@pytest.fixture
def fixture_root(tmp_path, monkeypatch):
    monkeypatch.setattr(vv, "ROOT", tmp_path)
    return tmp_path


# ── topology ────────────────────────────────────────────────────────────────

def test_topology_all_green(fixture_root):
    _repo_with_remote(fixture_root, "2-space", "datacore-space")
    _repo_with_remote(fixture_root, ".datacore/modules/mod", "datacore-mod")
    _registry(fixture_root, {
        "2-space": {"category": "knowledge", "repo": "datacore-space"},
        ".datacore/modules/mod": {"category": "code", "repo": "datacore-mod"},
        "not-here": {"category": "knowledge", "repo": "elsewhere"},
    })
    rep = vv.Report()
    vv.check_topology(rep)
    rows = {c.name: c for c in rep.checks}
    assert rows["remotes canonical"].ok is True and "2 repos" in rows["remotes canonical"].detail
    assert rows["knowledge on default branch"].ok is True
    assert rows["no stranded commits"].ok is True


def test_topology_wrong_remote_and_parked_knowledge(fixture_root):
    space = _repo_with_remote(fixture_root, "2-space", "someone-elses-fork")
    _git(space, "checkout", "-q", "-b", "wip")
    code = _repo_with_remote(fixture_root, ".datacore/modules/mod", "datacore-mod")
    _git(code, "checkout", "-q", "-b", "feature")
    _registry(fixture_root, {
        "2-space": {"category": "knowledge", "repo": "datacore-space"},
        ".datacore/modules/mod": {"category": "code", "repo": "datacore-mod"},
    })
    rep = vv.Report()
    vv.check_topology(rep)
    rows = {c.name: c for c in rep.checks}
    assert rows["remotes canonical"].ok is False
    assert "2-space: origin=someone-elses-fork registry=datacore-space" in rows["remotes canonical"].detail
    # A knowledge repo off its default branch fails; a code repo merely reports.
    assert rows["knowledge on default branch"].ok is False
    assert "2-space: wip" in rows["knowledge on default branch"].detail
    assert "mod" not in rows["knowledge on default branch"].detail.split("2-space: wip")[0]


def test_topology_code_parked_is_reported_not_failed(fixture_root):
    code = _repo_with_remote(fixture_root, ".datacore/modules/mod", "datacore-mod")
    _git(code, "checkout", "-q", "-b", "feature")
    _registry(fixture_root, {".datacore/modules/mod": {"category": "code", "repo": "datacore-mod"}})
    rep = vv.Report()
    vv.check_topology(rep)
    row = {c.name: c for c in rep.checks}["knowledge on default branch"]
    assert row.ok is True and "code parked: .datacore/modules/mod: feature" in row.detail


def test_stranded_commit_older_than_a_day_fails(fixture_root):
    space = _repo_with_remote(fixture_root, "2-space", "datacore-space")
    (space / "new").write_text("y\n")
    _git(space, "add", "new")
    old = str(int(time.time()) - 30 * 3600)
    _git(space, "commit", "-q", "-m", "local only",
         env={"GIT_COMMITTER_DATE": f"@{old} +0000", "GIT_AUTHOR_DATE": f"@{old} +0000"})
    n, age = vv.stranded_age_hours(space)
    assert n == 1 and 29 < age < 31
    _registry(fixture_root, {"2-space": {"category": "knowledge", "repo": "datacore-space"}})
    rep = vv.Report()
    vv.check_topology(rep)
    row = {c.name: c for c in rep.checks}["no stranded commits"]
    assert row.ok is False and row.detail.startswith("2-space: 1 commit(s), oldest 30h")


def test_fresh_unpushed_commit_is_not_stranded(fixture_root):
    space = _repo_with_remote(fixture_root, "2-space", "datacore-space")
    (space / "new").write_text("y\n")
    _git(space, "add", "new")
    _git(space, "commit", "-q", "-m", "just now")
    _registry(fixture_root, {"2-space": {"category": "knowledge", "repo": "datacore-space"}})
    rep = vv.Report()
    vv.check_topology(rep)
    assert {c.name: c for c in rep.checks}["no stranded commits"].ok is True


def test_topology_without_registry_is_not_applicable(fixture_root):
    rep = vv.Report()
    vv.check_topology(rep)
    assert rep.checks[0].name == "repo topology" and rep.checks[0].ok is None


# ── version parity ──────────────────────────────────────────────────────────

def _parity(monkeypatch, found: dict, pin=(0, 5, 1), mine="/py/a"):
    monkeypatch.setattr(vv, "interpreters", lambda: list(found))
    monkeypatch.setattr(vv, "org_workspace_version", lambda py: found[py])
    monkeypatch.setattr(vv, "_pinned_org_workspace", lambda: pin)
    monkeypatch.setattr(vv.sys, "executable", mine)
    monkeypatch.setattr(vv.os.path, "realpath", lambda p: p)
    rep = vv.Report()
    vv.check_versions(rep)
    return rep.checks[0]


def test_parity_one_version_everywhere(monkeypatch):
    row = _parity(monkeypatch, {"/py/a": "0.5.1", "/py/b": "0.5.1"})
    assert row.ok is True and row.detail == "0.5.1 under 2 interpreter(s)"


def test_parity_two_versions_fail(monkeypatch):
    row = _parity(monkeypatch, {"/py/a": "0.5.1", "/py/b": "0.4.4"})
    assert row.ok is False and "0.4.4 (/py/b)" in row.detail


def test_parity_below_pin_fails(monkeypatch):
    row = _parity(monkeypatch, {"/py/a": "0.4.4"}, pin=(0, 5, 1))
    assert row.ok is False and "below the pin 0.5.1" in row.detail


def test_parity_absent_under_own_interpreter_fails(monkeypatch):
    row = _parity(monkeypatch, {"/py/a": "missing", "/py/b": "0.5.1"}, mine="/py/a")
    assert row.ok is False and "absent under the checklist's own interpreter" in row.detail


def test_parity_absent_elsewhere_is_only_reported(monkeypatch):
    row = _parity(monkeypatch, {"/py/a": "0.5.1", "/py/b": "missing"})
    assert row.ok is True and "absent under 1" in row.detail


def test_parity_not_installed_anywhere_fails(monkeypatch):
    row = _parity(monkeypatch, {"/py/a": "missing"})
    assert row.ok is False and row.detail == "not installed for any interpreter"


# ── trust labels ────────────────────────────────────────────────────────────

def _roadmap(root: Path, space: str, items: list[dict]) -> None:
    import yaml
    (root / space).mkdir(parents=True, exist_ok=True)
    (root / space / "roadmap.yaml").write_text(yaml.safe_dump({"items": items}))


DONE_OK = {"id": "X-001", "status": "done", "shipped": "2026-09-01",
           "done_when": {"condition": "c", "evidence": "merged-pr", "verify": "gh pr view 1"}}


def test_done_with_evidence_and_verify_passes(fixture_root):
    _roadmap(fixture_root, "5-space", [DONE_OK, {"id": "X-002", "status": "ready"}])
    rep = vv.Report()
    vv.check_trust_labels(rep)
    assert rep.checks[0].ok is True and "1 roadmap(s)" in rep.checks[0].detail


def test_done_without_verify_or_shipped_fails(fixture_root):
    _roadmap(fixture_root, "5-space", [
        {"id": "X-003", "status": "done", "shipped": "2026-09-01",
         "done_when": {"condition": "c", "evidence": "test"}},
        {"id": "X-004", "status": "done", "shipped": None,
         "done_when": {"condition": "c", "evidence": "test", "verify": "pytest"}},
    ])
    assert vv.roadmap_trust_violations(fixture_root) == ["5-space:X-003", "5-space:X-004"]
    rep = vv.Report()
    vv.check_trust_labels(rep)
    assert rep.checks[0].ok is False and "5-space:X-003" in rep.checks[0].detail


def test_no_roadmap_is_not_applicable(fixture_root):
    rep = vv.Report()
    vv.check_trust_labels(rep)
    assert rep.checks[0].ok is None and rep.checks[0].skipped is True


def test_lens_ceiling_sits_above_the_measured_steady_state():
    assert vv.LENS_MAX_GB == 2.0
