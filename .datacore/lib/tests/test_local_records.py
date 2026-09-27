"""INS-3 batch 4: our own records live in gitignored `<name>.local.yaml` files
beside the tracked config, and every reader reads the local file too.

The tracked files ship neutral (no space, principal or repo of ours); the
reviewed commits, repo mappings and accepted findings of this installation sit
in .datacore/config/*.local.yaml, which .gitignore keeps out of the repo.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _config(root: Path) -> Path:
    d = root / ".datacore" / "config"
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_reviewed_commits_include_the_local_record(tmp_path, monkeypatch):
    import v2_verify
    cfg = _config(tmp_path)
    (cfg / "authorship-reviewed.yaml").write_text("version: 1\nreviewed:\n  - commit: aaaa1111\n")
    (cfg / "authorship-reviewed.local.yaml").write_text("version: 1\nreviewed:\n  - commit: bbbb2222\n")
    monkeypatch.setattr(v2_verify, "ROOT", tmp_path)
    assert v2_verify._reviewed_commits() == {"aaaa1111", "bbbb2222"}


def test_reviewed_commits_from_the_local_record_alone(tmp_path, monkeypatch):
    import v2_verify
    cfg = _config(tmp_path)
    (cfg / "authorship-reviewed.yaml").write_text("version: 1\nreviewed: []\n")
    (cfg / "authorship-reviewed.local.yaml").write_text("reviewed:\n  - commit: cccc3333\n")
    monkeypatch.setattr(v2_verify, "ROOT", tmp_path)
    assert v2_verify._reviewed_commits() == {"cccc3333"}


def test_repo_mapping_merges_the_local_file(tmp_path):
    import gh_reconcile
    cfg = _config(tmp_path)
    (cfg / "gh-reconcile.yaml").write_text("space_repos:\n  1-shared: org/shared\n")
    (cfg / "gh-reconcile.local.yaml").write_text(
        "space_repos:\n  1-mine: me/code\n\nextra_repos:\n  1-mine:\n    tool: me/tool\n")
    primary, secondary = gh_reconcile.load_config(tmp_path)
    assert primary == {"1-shared": ("org", "shared"), "1-mine": ("me", "code")}
    assert secondary == {"1-mine": {"tool": ("me", "tool")}}


def test_the_local_mapping_wins_for_a_space_in_both(tmp_path):
    import gh_reconcile
    cfg = _config(tmp_path)
    (cfg / "gh-reconcile.yaml").write_text("space_repos:\n  1-x: org/old\n")
    (cfg / "gh-reconcile.local.yaml").write_text("space_repos:\n  1-x: org/new\n")
    assert gh_reconcile.load_config(tmp_path)[0] == {"1-x": ("org", "new")}


def test_invariant_baseline_reads_the_local_sibling(tmp_path):
    import ledger_invariants as inv
    base = tmp_path / "ledger-invariants-baseline.yaml"
    base.write_text("version: 1\naccepted:\n  - {invariant: hashes, space: s1, detail_startswith: a}\n")
    (tmp_path / "ledger-invariants-baseline.local.yaml").write_text(
        "version: 1\naccepted:\n  - {invariant: declared, space: s2, detail_startswith: b}\n")
    assert [e["space"] for e in inv._baseline(base)] == ["s1", "s2"]


def test_the_shipped_configs_carry_no_records(tmp_path):
    """The tracked files are templates: every record is this install's own."""
    import yaml
    cfg = LIB.parent / "config"
    assert not (yaml.safe_load((cfg / "authorship-reviewed.yaml").read_text()) or {}).get("reviewed")
    assert not (yaml.safe_load((cfg / "ledger-invariants-baseline.yaml").read_text()) or {}).get("accepted")
    gh = yaml.safe_load((cfg / "gh-reconcile.yaml").read_text()) or {}
    assert not gh.get("space_repos") and not gh.get("extra_repos")
