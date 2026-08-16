"""Tests for marker-based space discovery (issue #41, DIP-0015)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from spaces import (  # noqa: E402
    MAX_DEPTH,
    Space,
    discover_spaces,
    discovery_discrepancy,
    find_space,
    read_marker,
)


def make_space(root: Path, rel: str, name: str, type_: str = "team", owner=None) -> Path:
    """Create a directory carrying a space marker."""
    path = root / rel
    (path / ".datacore").mkdir(parents=True, exist_ok=True)
    lines = ["space:", f"  name: {name}", f"  type: {type_}"]
    if owner:
        lines.append(f"  owner: {owner}")
    (path / ".datacore" / "config.yaml").write_text("\n".join(lines) + "\n")
    return path


# ── marker reading ───────────────────────────────────────────────────────────

def test_reads_marker(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    assert read_marker(tmp_path / "1-alpha") == {"name": "alpha", "type": "team"}


def test_directory_without_marker_is_not_a_space(tmp_path):
    (tmp_path / "plain").mkdir()
    assert read_marker(tmp_path / "plain") is None


def test_config_without_space_key_is_not_a_space(tmp_path):
    d = tmp_path / "1-alpha" / ".datacore"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("modules:\n  - x\n")
    assert read_marker(tmp_path / "1-alpha") is None


def test_malformed_marker_is_skipped_not_raised(tmp_path):
    """One bad file must not take out discovery for every other space."""
    d = tmp_path / "1-alpha" / ".datacore"
    d.mkdir(parents=True)
    (d / "config.yaml").write_text("space: [unclosed\n")
    make_space(tmp_path, "2-beta", "beta")

    assert read_marker(tmp_path / "1-alpha") is None
    assert [s.name for s in discover_spaces(tmp_path, include_legacy=False)] == ["beta"]


# ── discovery ────────────────────────────────────────────────────────────────

def test_finds_marked_spaces(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    make_space(tmp_path, "2-beta", "beta", type_="personal")

    found = discover_spaces(tmp_path, include_legacy=False)
    assert [(s.name, s.type) for s in found] == [("alpha", "team"), ("beta", "personal")]
    assert all(s.marked for s in found)


def test_client_space_nested_under_its_owner_is_found(tmp_path):
    """The real layout: <root>/<space>/1-tracks/clients/<name> is depth 4.

    Regression guard — this sat exactly on MAX_DEPTH when first shipped, so one
    extra grouping directory would have dropped it out of discovery silently.
    """
    make_space(tmp_path, "1-owner", "owner")
    deeper = make_space(
        tmp_path, "1-owner/1-tracks/clients/active/acme", "acme", type_="client"
    )
    assert deeper in [s.path for s in discover_spaces(tmp_path, include_legacy=False)]


def test_location_does_not_matter(tmp_path):
    """The whole point: a space need not sit at the install root."""
    make_space(tmp_path, "1-owner", "owner")
    nested = make_space(
        tmp_path, "1-owner/1-tracks/clients/acme", "acme", type_="client", owner="owner"
    )

    found = discover_spaces(tmp_path, include_legacy=False)
    assert nested in [s.path for s in found]

    acme = next(s for s in found if s.name == "acme")
    assert acme.type == "client"
    assert acme.owner == "owner"
    assert acme.ordinal is None  # no numeric prefix, and none needed


def test_type_filter(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha", type_="team")
    make_space(tmp_path, "2-beta", "beta", type_="client")

    assert [s.name for s in discover_spaces(tmp_path, types={"client"})] == ["beta"]


def test_skips_heavy_directories(tmp_path):
    """2-projects holds cloned repos; descending into it is the main walk cost."""
    make_space(tmp_path, "2-projects/vendored", "vendored")
    make_space(tmp_path, "1-alpha/node_modules/pkg", "pkg")
    make_space(tmp_path, "1-alpha", "alpha")

    assert [s.name for s in discover_spaces(tmp_path, include_legacy=False)] == ["alpha"]


def test_respects_depth_bound(tmp_path):
    deep = "/".join(f"d{i}" for i in range(MAX_DEPTH + 2))
    make_space(tmp_path, deep, "toodeep")
    assert discover_spaces(tmp_path, include_legacy=False) == []


def test_does_not_follow_symlinks(tmp_path):
    """A symlinked space would otherwise be discovered twice, at two paths."""
    real = make_space(tmp_path, "1-alpha", "alpha")
    (tmp_path / "link").symlink_to(real, target_is_directory=True)

    found = discover_spaces(tmp_path, include_legacy=False)
    assert [s.path for s in found] == [real]


# ── legacy union: migration must not lose anything ───────────────────────────

def make_legacy_space(root: Path, rel: str) -> Path:
    """Create a numbered directory that looks like a space but has no marker.

    Represents a real space that predates marker-based discovery — it carries
    canonical space artefacts (CLAUDE.base.md) but no ``.datacore/config.yaml``.
    A bare empty directory is NOT a legacy space: _looks_like_space() filters
    directories that have no space artefacts (stray sub-trees, accidental matches).
    """
    path = root / rel
    path.mkdir(parents=True, exist_ok=True)
    (path / "CLAUDE.base.md").write_text("# placeholder\n")
    return path


def test_union_includes_unmarked_legacy_dirs(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    make_legacy_space(tmp_path, "2-unmarked")

    names = {s.name for s in discover_spaces(tmp_path, include_legacy=True)}
    assert names == {"alpha", "unmarked"}

    unmarked = next(s for s in discover_spaces(tmp_path, include_legacy=True) if s.name == "unmarked")
    assert unmarked.marked is False
    assert unmarked.type == "unknown"


def test_marker_wins_over_legacy_for_same_directory(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    found = [s for s in discover_spaces(tmp_path) if s.path.name == "1-alpha"]
    assert len(found) == 1
    assert found[0].marked is True


def test_legacy_can_be_excluded(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    make_legacy_space(tmp_path, "2-unmarked")
    assert [s.name for s in discover_spaces(tmp_path, include_legacy=False)] == ["alpha"]


def test_type_filter_excludes_legacy(tmp_path):
    """Legacy dirs declare no type, so any type filter necessarily drops them."""
    make_legacy_space(tmp_path, "2-unmarked")
    assert discover_spaces(tmp_path, types={"team"}) == []


# ── discrepancy: the gate on dropping the glob ───────────────────────────────

def test_discrepancy_reports_what_would_vanish(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    make_legacy_space(tmp_path, "2-unmarked")

    marker_only, legacy_only = discovery_discrepancy(tmp_path)
    assert legacy_only == {tmp_path / "2-unmarked"}
    assert marker_only == set()


def test_bare_glob_match_not_treated_as_legacy_space(tmp_path):
    """An empty dir matching [0-9]-* is NOT a legacy space (no space artefacts).

    This prevents stray sub-tree directories (e.g. a ``1-tracks/`` leaked to
    the install root) from appearing as phantom spaces in discovery.
    """
    (tmp_path / "2-stray").mkdir()
    marker_only, legacy_only = discovery_discrepancy(tmp_path)
    assert legacy_only == set()
    assert discover_spaces(tmp_path, include_legacy=True) == []


def test_discrepancy_reports_what_the_glob_never_saw(tmp_path):
    make_space(tmp_path, "1-owner", "owner")
    nested = make_space(tmp_path, "1-owner/clients/acme", "acme", type_="client")

    marker_only, legacy_only = discovery_discrepancy(tmp_path)
    assert nested in marker_only
    assert legacy_only == set()


def test_discrepancy_empty_when_fully_migrated(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    make_space(tmp_path, "2-beta", "beta")
    assert discovery_discrepancy(tmp_path) == (set(), set())


# ── containment ──────────────────────────────────────────────────────────────

def test_find_space_returns_containing_space(tmp_path):
    alpha = make_space(tmp_path, "1-alpha", "alpha")
    target = alpha / "org" / "next_actions.org"
    target.parent.mkdir(parents=True)
    target.touch()

    assert find_space(target, tmp_path).name == "alpha"


def test_find_space_prefers_innermost(tmp_path):
    """A path inside a nested client space belongs to it, not to its owner."""
    make_space(tmp_path, "1-owner", "owner")
    acme = make_space(tmp_path, "1-owner/clients/acme", "acme", type_="client")

    assert find_space(acme / "notes.md", tmp_path).name == "acme"


def test_find_space_returns_none_outside_any_space(tmp_path):
    make_space(tmp_path, "1-alpha", "alpha")
    loose = tmp_path / "loose.md"
    loose.touch()
    assert find_space(loose, tmp_path) is None


# ── ordinal is cosmetic ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "dirname, expected",
    [("1-alpha", 1), ("12-alpha", 12), ("alpha", None), ("acme", None)],
)
def test_ordinal_parsing(tmp_path, dirname, expected):
    make_space(tmp_path, dirname, "x")
    space = discover_spaces(tmp_path, include_legacy=False)[0]
    assert space.ordinal == expected


def test_same_space_may_carry_different_ordinals_per_install(tmp_path):
    """Identity comes from the marker, never from the prefix."""
    a = make_space(tmp_path / "install-a", "5-thing", "thing")
    b = make_space(tmp_path / "install-b", "9-thing", "thing")

    name_a = discover_spaces(tmp_path / "install-a", include_legacy=False)[0]
    name_b = discover_spaces(tmp_path / "install-b", include_legacy=False)[0]

    assert name_a.name == name_b.name == "thing"
    assert (name_a.ordinal, name_b.ordinal) == (5, 9)
    assert a != b
