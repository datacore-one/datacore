"""The gitignored local records name a space by its stable NAME, never by the
number prefix of its folder (ENG-2026-08-03-047, ENG-2026-09-27-007).

The number is added locally and differs per host: the product space is
`5-plur` on the Mac and `3-plur` on hermes. So a record that says `plur` must
match the folder `3-plur`, and an older copy that still says `5-plur` must match
it too -- both sides are compared as bare names.
"""
from __future__ import annotations

import sys
from pathlib import Path

LIB = Path(__file__).resolve().parents[1]
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))


def _space(root: Path, folder: str, config_name: str | None = None) -> Path:
    d = root / folder
    (d / ".datacore").mkdir(parents=True, exist_ok=True)
    (d / "org").mkdir(exist_ok=True)
    if config_name:
        (d / ".datacore" / "config.yaml").write_text(f"space:\n  name: {config_name}\n")
    return d


# ── the shared rule ───────────────────────────────────────────────────────────

def test_same_space_compares_bare_names(tmp_path):
    from spaces import same_space
    folder = _space(tmp_path, "3-plur")
    assert same_space("plur", folder)
    assert same_space("5-plur", folder)       # legacy value from another host
    assert same_space("3-plur", folder)
    assert not same_space("plur-space", folder)
    assert not same_space("", folder)
    assert not same_space(None, folder)


def test_same_space_honours_the_configured_name(tmp_path):
    from spaces import same_space
    folder = _space(tmp_path, "4-firm", config_name="firm")
    assert same_space("firm", folder)
    assert same_space("8-firm", folder)


# ── ledger_invariants: the accepted-findings baseline ─────────────────────────

def _finding(space_folder: str):
    import ledger_invariants as inv
    return inv.Finding("hashes", space_folder, "tris seq 5: payload hash mismatch")


def test_baseline_bare_name_matches_a_differently_numbered_folder(tmp_path):
    import ledger_invariants as inv
    _space(tmp_path, "3-plur")
    entry = [{"invariant": "hashes", "space": "plur", "detail_startswith": "tris seq 5"}]
    assert inv._accepted(_finding("3-plur"), entry, tmp_path)


def test_baseline_legacy_numbered_value_matches_another_hosts_folder(tmp_path):
    import ledger_invariants as inv
    _space(tmp_path, "3-plur")
    entry = [{"invariant": "hashes", "space": "5-plur", "detail_startswith": "tris seq 5"}]
    assert inv._accepted(_finding("3-plur"), entry, tmp_path)


def test_baseline_still_rejects_another_space(tmp_path):
    import ledger_invariants as inv
    _space(tmp_path, "3-plur")
    entry = [{"invariant": "hashes", "space": "datacore", "detail_startswith": "tris seq 5"}]
    assert not inv._accepted(_finding("3-plur"), entry, tmp_path)


# ── gh_reconcile: the space -> repo mapping ───────────────────────────────────

def _gh_local(root: Path, key: str) -> None:
    cfg = root / ".datacore" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "gh-reconcile.local.yaml").write_text(
        f"space_repos:\n  {key}: plur-ai/plur\n\nextra_repos:\n  {key}:\n    enterprise: plur-ai/enterprise\n")


def test_repo_mapping_bare_name_matches_a_differently_numbered_folder(tmp_path):
    import gh_reconcile
    folder = _space(tmp_path, "3-plur")
    _gh_local(tmp_path, "plur")
    primary, secondary = gh_reconcile.load_config(tmp_path)
    assert gh_reconcile.for_space(primary, folder) == ("plur-ai", "plur")
    assert gh_reconcile.for_space(secondary, folder) == {"enterprise": ("plur-ai", "enterprise")}


def test_repo_mapping_legacy_numbered_key_matches_another_hosts_folder(tmp_path):
    import gh_reconcile
    folder = _space(tmp_path, "3-plur")
    _gh_local(tmp_path, "5-plur")
    primary, secondary = gh_reconcile.load_config(tmp_path)
    assert gh_reconcile.for_space(primary, folder) == ("plur-ai", "plur")
    assert gh_reconcile.for_space(secondary, folder) == {"enterprise": ("plur-ai", "enterprise")}


def test_repo_mapping_has_nothing_for_an_unmapped_space(tmp_path):
    import gh_reconcile
    folder = _space(tmp_path, "2-datacore")
    _gh_local(tmp_path, "plur")
    primary, _ = gh_reconcile.load_config(tmp_path)
    assert gh_reconcile.for_space(primary, folder) is None


# ── v2_verify: reviewed commits are keyed by commit, not by space ─────────────

def test_reviewed_commits_do_not_depend_on_the_space_field(tmp_path, monkeypatch):
    import v2_verify
    _space(tmp_path, "3-plur")
    cfg = tmp_path / ".datacore" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "authorship-reviewed.local.yaml").write_text(
        "reviewed:\n  - {commit: aaaa1111, space: plur}\n  - {commit: bbbb2222, space: 5-plur}\n")
    monkeypatch.setattr(v2_verify, "ROOT", tmp_path)
    assert v2_verify._reviewed_commits() == {"aaaa1111", "bbbb2222"}
