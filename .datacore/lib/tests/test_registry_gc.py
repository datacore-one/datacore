"""Tests for registry_gc.py — lifecycle enforcement for the agent registry.

Fixture registry mirrors the real DIP-0016 shape read from
.datacore/registry/agents.yaml: a top-level mapping keyed by agent name,
each entry carrying description/version/source/skills/etc., with the
registered agents living under `agents:`. Everything here is built inside
tmp_path — no real registry or agent files are touched (that's task 7.3).
"""

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
import registry_gc  # noqa: E402
from registry_gc import DuplicateKeyError, GcReport, apply, audit, main  # noqa: E402


AGENTS_YAML = """\
version: "2.0.0"
protocol: "DIP-0016"
updated: "2026-07-29"

defaults:
  hooks:
    pre: []

agents:
  valid-agent:
    description: "A perfectly normal valid agent"
    version: "1.0.0"
    source: ".datacore/agents/valid-agent.md"
    skills:
    - testing

  deprecated-field-agent:
    description: "Deprecated via explicit status field"
    version: "1.0.0"
    source: ".datacore/agents/deprecated-field-agent.md"
    status: deprecated
    skills:
    - testing

  "[DEPRECATED] deprecated-name-agent":
    description: "Deprecated via marker in the entry name"
    version: "1.0.0"
    source: ".datacore/agents/deprecated-name-agent.md"
    skills:
    - testing

  deprecated-desc-agent:
    description: "[DEPRECATED] superseded by valid-agent"
    version: "1.0.0"
    source: ".datacore/agents/deprecated-desc-agent.md"
    skills:
    - testing

  orphaned-agent:
    description: "Entry whose source file no longer exists on disk"
    version: "1.0.0"
    source: ".datacore/agents/missing-agent.md"
    skills:
    - testing
"""

DEPRECATED_NAMES = [
    "deprecated-field-agent",
    "[DEPRECATED] deprecated-name-agent",
    "deprecated-desc-agent",
]

# Every GcReport identifier is now "<section>/<name>" (Controller Scope
# Amendment: registry_gc covers both agents:/module_agents: sections, and
# report/action-log identifiers always carry their section prefix so
# 7.3's records are unambiguous). DEPRECATED_NAMES stays bare — it's used
# throughout to check the RAW reloaded/archived YAML dict keys, which are
# real YAML keys, never prefixed. DEPRECATED_IDS is the report-facing form.
DEPRECATED_IDS = [f"agents/{n}" for n in DEPRECATED_NAMES]

DEF_FILE_AGENTS = [
    "valid-agent",
    "deprecated-field-agent",
    "deprecated-name-agent",
    "deprecated-desc-agent",
]


def build_fixture(root: Path) -> dict:
    """Build a fixture repo tree under `root` with all GC categories:
    valid, deprecated-by-field, deprecated-by-name, deprecated-by-description,
    orphaned, unregistered, and stray .bak files."""
    registry_dir = root / ".datacore" / "registry"
    agents_dir = root / ".datacore" / "agents"
    registry_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)

    registry_path = registry_dir / "agents.yaml"
    registry_path.write_text(AGENTS_YAML, encoding="utf-8")

    # stray .bak files in the registry dir
    (registry_dir / "agents.yaml.bak").write_text("# stale backup\n", encoding="utf-8")
    (registry_dir / "agents.yaml.bak-2026-06-10").write_text(
        "# stale backup\n", encoding="utf-8"
    )

    for name in DEF_FILE_AGENTS:
        (agents_dir / f"{name}.md").write_text(
            f"# {name}\n\nAgent definition body.\n", encoding="utf-8"
        )
    # unregistered: file on disk with no registry entry
    (agents_dir / "unregistered-agent.md").write_text(
        "# unregistered-agent\n\nNo registry entry references this file.\n",
        encoding="utf-8",
    )
    # orphaned-agent's source (missing-agent.md) is deliberately never created

    return {
        "root": root,
        "registry_path": registry_path,
        "registry_dir": registry_dir,
        "agents_dir": agents_dir,
        "archive_dir": root / ".datacore" / "agents" / "_deprecated",
    }


def build_clean_fixture(root: Path) -> dict:
    """Fixture with only a valid entry + an unregistered file — no
    deprecated, orphaned, or .bak categories. Used for the
    unregistered-only --check exit-0 case."""
    registry_dir = root / ".datacore" / "registry"
    agents_dir = root / ".datacore" / "agents"
    registry_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)

    registry_path = registry_dir / "agents.yaml"
    registry_path.write_text(
        """\
version: "2.0.0"
protocol: "DIP-0016"
agents:
  valid-agent:
    description: "A perfectly normal valid agent"
    version: "1.0.0"
    source: ".datacore/agents/valid-agent.md"
""",
        encoding="utf-8",
    )
    (agents_dir / "valid-agent.md").write_text("# valid-agent\n", encoding="utf-8")
    (agents_dir / "unregistered-agent.md").write_text(
        "# unregistered-agent\n", encoding="utf-8"
    )
    return {
        "root": root,
        "registry_path": registry_path,
        "agents_dir": agents_dir,
        "archive_dir": root / ".datacore" / "agents" / "_deprecated",
    }


SHARED_SOURCE_YAML = """\
version: "2.0.0"
protocol: "DIP-0016"
agents:
  survivor-agent:
    description: "Still valid, shares a source file with a deprecated entry"
    version: "1.0.0"
    source: ".datacore/agents/shared-agent.md"
  deprecated-shared-agent:
    description: "Deprecated but its source file is still referenced by survivor-agent"
    version: "1.0.0"
    source: ".datacore/agents/shared-agent.md"
    status: deprecated
"""


def build_shared_source_fixture(root: Path) -> dict:
    """Two entries pointing at the SAME source file, one deprecated, one a
    survivor — the shared-source collision guard must leave the file alone."""
    registry_dir = root / ".datacore" / "registry"
    agents_dir = root / ".datacore" / "agents"
    registry_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    registry_path = registry_dir / "agents.yaml"
    registry_path.write_text(SHARED_SOURCE_YAML, encoding="utf-8")
    (agents_dir / "shared-agent.md").write_text("# shared-agent\n", encoding="utf-8")
    return {
        "root": root,
        "registry_path": registry_path,
        "registry_dir": registry_dir,
        "agents_dir": agents_dir,
        "archive_dir": root / ".datacore" / "agents" / "_deprecated",
    }


def build_archive_collision_fixture(root: Path) -> dict:
    """build_fixture() plus an UNRELATED, pre-existing file already
    sitting in archive_dir under the exact same basename as one of the
    deprecated entries' def file — the Task 7.3 production incident this
    guards against (gtd-research-processor-module's move silently
    overwrote an unrelated archived file of the same name)."""
    fx = build_fixture(root)
    fx["archive_dir"].mkdir(parents=True, exist_ok=True)
    (fx["archive_dir"] / "deprecated-field-agent.md").write_text(
        "# UNRELATED pre-existing archived agent\n\n"
        "Completely different content — must never be overwritten.\n",
        encoding="utf-8",
    )
    return fx


def build_gitignored_source_fixture(root: Path) -> dict:
    """A deprecated entry whose source file sits under a path the repo's
    OWN `.gitignore` excludes — the gitignored-source guard must archive
    the entry as metadata only, leaving the file physically in place
    rather than moving it into the tracked `archive_dir`. `root` is
    `git init`'d and given a real `.gitignore` so `git check-ignore`
    resolves it exactly as it would in a real repo."""
    registry_dir = root / ".datacore" / "registry"
    agents_dir = root / ".datacore" / "agents"
    registry_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)

    registry_path = registry_dir / "agents.yaml"
    registry_path.write_text(
        """\
version: "2.0.0"
protocol: "DIP-0016"
agents:
  ignored-deprecated-agent:
    description: "Deprecated, but its source lives under a gitignored path"
    version: "1.0.0"
    source: ".datacore/agents/ignored-deprecated-agent.md"
    status: deprecated
""",
        encoding="utf-8",
    )
    (agents_dir / "ignored-deprecated-agent.md").write_text(
        "# ignored-deprecated-agent\n", encoding="utf-8"
    )

    (root / ".gitignore").write_text(
        "/.datacore/agents/ignored-deprecated-agent.md\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)

    return {
        "root": root,
        "registry_path": registry_path,
        "registry_dir": registry_dir,
        "agents_dir": agents_dir,
        "archive_dir": root / ".datacore" / "agents" / "_deprecated",
    }


HEADER_COMMENT_LINES = [
    "# =====================================",
    "# FIXTURE REGISTRY HEADER",
    "# multi-line comment block, preserved verbatim",
    "# =====================================",
]


def build_fixture_with_header(root: Path) -> dict:
    """Same as build_fixture(), but agents.yaml carries a leading
    multi-line comment header block, mirroring the real registry's
    DIP-0016 documentation header."""
    fx = build_fixture(root)
    original = fx["registry_path"].read_text(encoding="utf-8")
    header_text = "\n".join(HEADER_COMMENT_LINES) + "\n\n"
    fx["registry_path"].write_text(header_text + original, encoding="utf-8")
    return fx


def build_duplicate_key_fixture(root: Path) -> dict:
    """build_fixture() plus a second, duplicated top-level `agents:` block
    appended — simulates a bad merge silently dropping half the registry."""
    fx = build_fixture(root)
    text = fx["registry_path"].read_text(encoding="utf-8")
    text += (
        "\nagents:\n"
        '  extra-agent:\n'
        '    description: "duplicate top-level agents block"\n'
        '    version: "1.0.0"\n'
    )
    fx["registry_path"].write_text(text, encoding="utf-8")
    return fx


DUAL_SECTION_YAML = """\
version: "2.0.0"
protocol: "DIP-0016"
agents:
  core-valid-agent:
    description: "Valid core agent"
    version: "1.0.0"
    source: ".datacore/agents/core-valid-agent.md"
  core-deprecated-agent:
    description: "Deprecated core agent"
    version: "1.0.0"
    source: ".datacore/agents/core-deprecated-agent.md"
    status: deprecated
  core-orphaned-agent:
    description: "Orphaned core agent"
    version: "1.0.0"
    source: ".datacore/agents/core-missing-agent.md"
module_agents:
  mod-valid-agent:
    description: "Valid module agent"
    version: "1.0.0"
    source: ".datacore/modules/demo/agents/mod-valid-agent.md"
    module: "demo"
  mod-deprecated-agent:
    description: "[DEPRECATED] superseded"
    version: "1.0.0"
    source: ".datacore/modules/demo/agents/mod-deprecated-agent.md"
    module: "demo"
  mod-orphaned-agent:
    description: "Orphaned module agent"
    version: "1.0.0"
    source: ".datacore/modules/demo/agents/mod-missing-agent.md"
    module: "demo"
"""


def build_dual_section_fixture(root: Path) -> dict:
    """Entries in BOTH `agents:` and `module_agents:` — deprecated,
    orphaned, and valid in EACH section. Exercises the Controller Scope
    Amendment (module_agents coverage) end to end."""
    registry_dir = root / ".datacore" / "registry"
    core_agents_dir = root / ".datacore" / "agents"
    module_agents_dir = root / ".datacore" / "modules" / "demo" / "agents"
    registry_dir.mkdir(parents=True)
    core_agents_dir.mkdir(parents=True)
    module_agents_dir.mkdir(parents=True)

    registry_path = registry_dir / "agents.yaml"
    registry_path.write_text(DUAL_SECTION_YAML, encoding="utf-8")

    for name in ("core-valid-agent", "core-deprecated-agent"):
        (core_agents_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    for name in ("mod-valid-agent", "mod-deprecated-agent"):
        (module_agents_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    # core-orphaned-agent / mod-orphaned-agent's sources are deliberately
    # never created

    return {
        "root": root,
        "registry_path": registry_path,
        "registry_dir": registry_dir,
        "core_agents_dir": core_agents_dir,
        "module_agents_dir": module_agents_dir,
        "archive_dir": root / ".datacore" / "agents" / "_deprecated",
    }


def build_nested_duplicate_fixture(root: Path) -> dict:
    """A single section (module_agents:) with the SAME second-level entry
    name appearing twice — a bad merge duplicating one agent entry within
    a section, without duplicating the section's own top-level key (that
    case is already covered by build_duplicate_key_fixture)."""
    registry_dir = root / ".datacore" / "registry"
    agents_dir = root / ".datacore" / "agents"
    registry_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    registry_path = registry_dir / "agents.yaml"
    registry_path.write_text(
        """\
version: "2.0.0"
protocol: "DIP-0016"
agents:
  valid-agent:
    description: "A perfectly normal valid agent"
    version: "1.0.0"
    source: ".datacore/agents/valid-agent.md"
module_agents:
  evaluator-critic:
    description: "First copy"
    version: "1.0.0"
    source: ".datacore/modules/nightshift/agents/evaluator-critic.md"
    module: "nightshift"
  evaluator-critic:
    description: "Second, duplicate copy from a bad merge"
    version: "1.0.0"
    source: ".datacore/modules/nightshift/agents/evaluator-critic.md"
    module: "nightshift"
""",
        encoding="utf-8",
    )
    (agents_dir / "valid-agent.md").write_text("# valid-agent\n", encoding="utf-8")
    return {
        "root": root,
        "registry_path": registry_path,
        "agents_dir": agents_dir,
        "archive_dir": root / ".datacore" / "agents" / "_deprecated",
    }


def snapshot_tree(root: Path) -> dict:
    """Map every file under root to its raw bytes, for byte-identical
    before/after comparisons."""
    snap = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            snap[str(p.relative_to(root))] = p.read_bytes()
    return snap


# ---------------------------------------------------------------------------
# audit()
# ---------------------------------------------------------------------------


class TestAudit:
    def test_classifies_all_categories(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])

        assert isinstance(report, GcReport)
        assert sorted(report.deprecated) == sorted(DEPRECATED_IDS)
        assert report.orphaned_entries == ["agents/orphaned-agent"]
        assert len(report.bak_files) == 2
        assert any(b.endswith("agents.yaml.bak") for b in report.bak_files)
        assert any(b.endswith("agents.yaml.bak-2026-06-10") for b in report.bak_files)
        assert len(report.unregistered_files) == 1
        assert report.unregistered_files[0].endswith("unregistered-agent.md")
        # only valid-agent is neither deprecated nor orphaned
        assert report.active_count == 1

    def test_does_not_write_anything(self, tmp_path):
        fx = build_fixture(tmp_path)
        before = snapshot_tree(tmp_path)
        audit(fx["registry_path"], [fx["agents_dir"]])
        after = snapshot_tree(tmp_path)
        assert before == after

    def test_unregistered_only_has_empty_other_categories(self, tmp_path):
        fx = build_clean_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report.deprecated == []
        assert report.orphaned_entries == []
        assert report.bak_files == []
        assert report.unregistered_files == ["unregistered-agent.md"] or (
            len(report.unregistered_files) == 1
            and report.unregistered_files[0].endswith("unregistered-agent.md")
        )
        assert report.active_count == 1

    def test_no_agents_dirs_skips_unregistered_check(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [])
        assert report.unregistered_files == []
        # other categories unaffected
        assert sorted(report.deprecated) == sorted(DEPRECATED_IDS)

    def test_entry_with_no_source_field_is_orphaned(self, tmp_path):
        registry_dir = tmp_path / ".datacore" / "registry"
        agents_dir = tmp_path / ".datacore" / "agents"
        registry_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)
        registry_path = registry_dir / "agents.yaml"
        registry_path.write_text(
            """\
version: "2.0.0"
agents:
  no-source-agent:
    description: "Entry missing the source field entirely"
    version: "1.0.0"
""",
            encoding="utf-8",
        )
        report = audit(registry_path, [agents_dir])
        assert report.orphaned_entries == ["agents/no-source-agent"]
        assert report.deprecated == []

    def test_deprecated_entry_with_missing_source_is_deprecated_only(self, tmp_path):
        """Deprecated classification takes strict priority over orphaned:
        an entry that is BOTH deprecated AND missing its source file is
        reported only as deprecated, never double-counted as orphaned."""
        registry_dir = tmp_path / ".datacore" / "registry"
        agents_dir = tmp_path / ".datacore" / "agents"
        registry_dir.mkdir(parents=True)
        agents_dir.mkdir(parents=True)
        registry_path = registry_dir / "agents.yaml"
        registry_path.write_text(
            """\
version: "2.0.0"
agents:
  gone-and-deprecated:
    description: "Deprecated AND its source file no longer exists"
    version: "1.0.0"
    source: ".datacore/agents/gone-and-deprecated.md"
    status: deprecated
""",
            encoding="utf-8",
        )
        report = audit(registry_path, [agents_dir])
        assert report.deprecated == ["agents/gone-and-deprecated"]
        assert report.orphaned_entries == []

        archive_dir = tmp_path / ".datacore" / "agents" / "_deprecated"
        actions = apply(report, registry_path, archive_dir)
        assert any(
            "archived deprecated entry 'agents/gone-and-deprecated'" in a for a in actions
        )
        reloaded = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        assert "gone-and-deprecated" not in reloaded["agents"]


# ---------------------------------------------------------------------------
# apply()
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_archives_deprecated_entries(self, tmp_path):
        fx = build_fixture(tmp_path)
        original = yaml.safe_load(AGENTS_YAML)["agents"]
        report = audit(fx["registry_path"], [fx["agents_dir"]])

        apply(report, fx["registry_path"], fx["archive_dir"])

        archive_yaml = fx["registry_dir"] / "archive" / "agents-deprecated.yaml"
        assert archive_yaml.exists()
        archived = yaml.safe_load(archive_yaml.read_text(encoding="utf-8"))
        assert set(archived["agents"].keys()) == set(DEPRECATED_NAMES)
        for name in DEPRECATED_NAMES:
            assert archived["agents"][name]["description"] == original[name]["description"]

    def test_apply_moves_def_files_preserving_filename(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        assert not (fx["agents_dir"] / "deprecated-field-agent.md").exists()
        assert not (fx["agents_dir"] / "deprecated-name-agent.md").exists()
        assert not (fx["agents_dir"] / "deprecated-desc-agent.md").exists()
        assert (fx["archive_dir"] / "deprecated-field-agent.md").exists()
        assert (fx["archive_dir"] / "deprecated-name-agent.md").exists()
        assert (fx["archive_dir"] / "deprecated-desc-agent.md").exists()
        # valid-agent's def file untouched
        assert (fx["agents_dir"] / "valid-agent.md").exists()

    def test_apply_gitignored_source_retained_not_moved(self, tmp_path):
        """A deprecated entry whose source file is git-ignored must be
        archived as metadata only -- the physical move is skipped, the
        file stays exactly where it was, and a WARNING line says so."""
        fx = build_gitignored_source_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report.deprecated == ["agents/ignored-deprecated-agent"]

        actions = apply(report, fx["registry_path"], fx["archive_dir"])

        original = fx["agents_dir"] / "ignored-deprecated-agent.md"
        assert original.exists(), "gitignored source must be left in place"
        assert not (fx["archive_dir"] / "ignored-deprecated-agent.md").exists()

        archive_yaml = fx["registry_dir"] / "archive" / "agents-deprecated.yaml"
        archived = yaml.safe_load(archive_yaml.read_text(encoding="utf-8"))
        assert "ignored-deprecated-agent" in archived["agents"]
        # source: field still points at the ORIGINAL (unmoved) location
        assert (
            archived["agents"]["ignored-deprecated-agent"]["source"]
            == ".datacore/agents/ignored-deprecated-agent.md"
        )

        assert any(
            "[gc] WARNING gitignored source retained:" in a
            and "ignored-deprecated-agent.md" in a
            and "archive entry created, file left in place" in a
            for a in actions
        )

        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        assert "ignored-deprecated-agent" not in reloaded["agents"]

    def test_apply_removes_deprecated_and_orphaned_from_registry(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        remaining = reloaded["agents"]
        for name in DEPRECATED_NAMES + ["orphaned-agent"]:
            assert name not in remaining
        assert "valid-agent" in remaining

    def test_apply_preserves_valid_entry_semantically(self, tmp_path):
        fx = build_fixture(tmp_path)
        original = yaml.safe_load(AGENTS_YAML)["agents"]["valid-agent"]
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        assert reloaded["agents"]["valid-agent"] == original

    def test_apply_orphaned_leaves_comment_note_not_data(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        archive_yaml = fx["registry_dir"] / "archive" / "agents-deprecated.yaml"
        raw = archive_yaml.read_text(encoding="utf-8")
        assert "orphaned-agent" in raw
        parsed = yaml.safe_load(raw)
        # the note is a comment, never promoted into the loadable agents mapping
        assert "orphaned-agent" not in (parsed.get("agents") or {})

    def test_apply_deletes_bak_files(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        assert not (fx["registry_dir"] / "agents.yaml.bak").exists()
        assert not (fx["registry_dir"] / "agents.yaml.bak-2026-06-10").exists()

    def test_apply_never_touches_unregistered_files(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        unreg = fx["agents_dir"] / "unregistered-agent.md"
        assert unreg.exists()
        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        assert "unregistered-agent" not in reloaded["agents"]

    def test_apply_returns_action_log(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        actions = apply(report, fx["registry_path"], fx["archive_dir"])
        assert isinstance(actions, list)
        assert len(actions) > 0

    def test_second_apply_is_noop_tree_byte_identical(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        after_first = snapshot_tree(tmp_path)

        report2 = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report2.deprecated == []
        assert report2.orphaned_entries == []
        assert report2.bak_files == []

        actions2 = apply(report2, fx["registry_path"], fx["archive_dir"])
        assert actions2 == []

        after_second = snapshot_tree(tmp_path)
        assert after_first == after_second

    def test_apply_with_empty_report_touches_nothing(self, tmp_path):
        fx = build_clean_fixture(tmp_path)
        before = snapshot_tree(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        actions = apply(report, fx["registry_path"], fx["archive_dir"])
        assert actions == []
        after = snapshot_tree(tmp_path)
        assert before == after


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    def test_check_exits_1_when_actionable(self, tmp_path, capsys):
        fx = build_fixture(tmp_path)
        rc = main(
            [
                "--registry",
                str(fx["registry_path"]),
                "--agents-dir",
                str(fx["agents_dir"]),
                "--check",
            ]
        )
        assert rc == 1

    def test_check_exits_0_when_unregistered_only(self, tmp_path, capsys):
        fx = build_clean_fixture(tmp_path)
        rc = main(
            [
                "--registry",
                str(fx["registry_path"]),
                "--agents-dir",
                str(fx["agents_dir"]),
                "--check",
            ]
        )
        assert rc == 0

    def test_check_exits_0_on_fully_clean_registry(self, tmp_path, capsys):
        fx = build_clean_fixture(tmp_path)
        # remove the unregistered file too -> nothing actionable at all
        (fx["agents_dir"] / "unregistered-agent.md").unlink()
        rc = main(
            [
                "--registry",
                str(fx["registry_path"]),
                "--agents-dir",
                str(fx["agents_dir"]),
                "--check",
            ]
        )
        assert rc == 0

    def test_apply_flag_mutates_registry_and_exits_0(self, tmp_path, capsys):
        fx = build_fixture(tmp_path)
        rc = main(
            [
                "--registry",
                str(fx["registry_path"]),
                "--agents-dir",
                str(fx["agents_dir"]),
                "--archive-dir",
                str(fx["archive_dir"]),
                "--apply",
            ]
        )
        assert rc == 0
        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        for name in DEPRECATED_NAMES + ["orphaned-agent"]:
            assert name not in reloaded["agents"]

    def test_apply_without_archive_dir_errors(self, tmp_path, capsys):
        fx = build_fixture(tmp_path)
        with pytest.raises(SystemExit):
            main(
                [
                    "--registry",
                    str(fx["registry_path"]),
                    "--agents-dir",
                    str(fx["agents_dir"]),
                    "--apply",
                ]
            )

    def test_check_and_apply_together_rejected_check_first(self, tmp_path, capsys):
        fx = build_fixture(tmp_path)
        with pytest.raises(SystemExit):
            main(
                [
                    "--registry",
                    str(fx["registry_path"]),
                    "--agents-dir",
                    str(fx["agents_dir"]),
                    "--archive-dir",
                    str(fx["archive_dir"]),
                    "--check",
                    "--apply",
                ]
            )

    def test_check_and_apply_together_rejected_apply_first(self, tmp_path, capsys):
        fx = build_fixture(tmp_path)
        with pytest.raises(SystemExit):
            main(
                [
                    "--registry",
                    str(fx["registry_path"]),
                    "--agents-dir",
                    str(fx["agents_dir"]),
                    "--archive-dir",
                    str(fx["archive_dir"]),
                    "--apply",
                    "--check",
                ]
            )


# ---------------------------------------------------------------------------
# Critical 1: shared source: collision guard
# ---------------------------------------------------------------------------


class TestSharedSourceCollision:
    def test_shared_source_file_retained_and_warned(self, tmp_path):
        fx = build_shared_source_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report.deprecated == ["agents/deprecated-shared-agent"]

        actions = apply(report, fx["registry_path"], fx["archive_dir"])

        # file untouched — still at its original location, not archived
        shared_file = fx["agents_dir"] / "shared-agent.md"
        assert shared_file.exists()
        assert not (fx["archive_dir"] / "shared-agent.md").exists()

        # warning emitted, naming both the path and the surviving entry
        assert any(
            "WARNING shared source retained" in a
            and "shared-agent.md" in a
            and "survivor-agent" in a
            for a in actions
        )

        # deprecated entry still archived (metadata only, source unmoved)
        archive_yaml = fx["registry_dir"] / "archive" / "agents-deprecated.yaml"
        archived = yaml.safe_load(archive_yaml.read_text(encoding="utf-8"))
        assert "deprecated-shared-agent" in archived["agents"]

        # survivor still resolves cleanly on re-audit
        report2 = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report2.orphaned_entries == []
        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        assert "survivor-agent" in reloaded["agents"]
        assert "deprecated-shared-agent" not in reloaded["agents"]


# ---------------------------------------------------------------------------
# Post-Task-7.3 production incident fix: archive destination collision guard
# ---------------------------------------------------------------------------


class TestArchiveDestinationCollisionGuard:
    """Task 7.3's real --apply run silently overwrote an unrelated,
    pre-existing archived def file (gtd-research-processor-module) that
    happened to share the exact same basename as a newly-archived entry's
    def file — recovered manually. These tests lock in the fix: a move
    into archive_dir must NEVER overwrite whatever is already there."""

    def test_pre_existing_archive_file_is_never_overwritten(self, tmp_path):
        fx = build_archive_collision_fixture(tmp_path)
        pre_existing_path = fx["archive_dir"] / "deprecated-field-agent.md"
        pre_existing_bytes = pre_existing_path.read_bytes()
        live_file_bytes = (fx["agents_dir"] / "deprecated-field-agent.md").read_bytes()

        report = audit(fx["registry_path"], [fx["agents_dir"]])
        actions = apply(report, fx["registry_path"], fx["archive_dir"])

        # the pre-existing, unrelated archived file is completely untouched
        assert pre_existing_path.read_bytes() == pre_existing_bytes

        # the newly-archived (different) entry landed under a
        # non-colliding suffixed name instead, with its OWN content
        suffixed = fx["archive_dir"] / "deprecated-field-agent-2.md"
        assert suffixed.exists()
        assert suffixed.read_bytes() == live_file_bytes
        assert suffixed.read_bytes() != pre_existing_bytes

        # live source file is gone (it really was moved, just renamed)
        assert not (fx["agents_dir"] / "deprecated-field-agent.md").exists()

        # action log names the chosen (suffixed) destination
        assert any("deprecated-field-agent-2.md" in a for a in actions)
        assert any("WARNING archive destination collision" in a for a in actions)

        # registry metadata correctly points at the suffixed location
        archive_yaml = fx["registry_dir"] / "archive" / registry_gc.ARCHIVE_YAML_NAME
        archived = yaml.safe_load(archive_yaml.read_text(encoding="utf-8"))
        assert archived["agents"]["deprecated-field-agent"]["source"].endswith(
            "deprecated-field-agent-2.md"
        )

    def test_multiple_collisions_increment_the_suffix(self, tmp_path):
        fx = build_archive_collision_fixture(tmp_path)
        (fx["archive_dir"] / "deprecated-field-agent-2.md").write_text(
            "# ANOTHER unrelated file already occupying the -2 suffix too\n",
            encoding="utf-8",
        )
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        assert (fx["archive_dir"] / "deprecated-field-agent-3.md").exists()
        # both pre-existing collision files remain exactly as they were
        assert (fx["archive_dir"] / "deprecated-field-agent.md").read_text(
            encoding="utf-8"
        ).startswith("# UNRELATED")
        assert (fx["archive_dir"] / "deprecated-field-agent-2.md").read_text(
            encoding="utf-8"
        ).startswith("# ANOTHER unrelated")

    def test_no_collision_still_uses_the_plain_basename(self, tmp_path):
        """Regression guard: the common case (no pre-existing file at the
        destination) must still land at the exact original filename, no
        suffix, no spurious warning."""
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        actions = apply(report, fx["registry_path"], fx["archive_dir"])

        assert (fx["archive_dir"] / "deprecated-field-agent.md").exists()
        assert not (fx["archive_dir"] / "deprecated-field-agent-2.md").exists()
        assert not any("WARNING archive destination collision" in a for a in actions)


# ---------------------------------------------------------------------------
# Critical 2: atomic writes
# ---------------------------------------------------------------------------


class TestAtomicWrites:
    def test_registry_replace_crash_leaves_original_intact(self, tmp_path, monkeypatch):
        fx = build_fixture(tmp_path)
        original_bytes = fx["registry_path"].read_bytes()
        report = audit(fx["registry_path"], [fx["agents_dir"]])

        real_replace = os.replace

        def boom(src, dst, *a, **kw):
            if str(fx["registry_path"]) == str(dst):
                raise OSError("simulated crash during registry replace")
            return real_replace(src, dst, *a, **kw)

        monkeypatch.setattr(registry_gc.os, "replace", boom)

        with pytest.raises(OSError):
            apply(report, fx["registry_path"], fx["archive_dir"])

        assert fx["registry_path"].read_bytes() == original_bytes
        # no leftover temp file next to the registry
        leftovers = [
            p for p in fx["registry_dir"].iterdir() if p.name.startswith(".agents.yaml.")
        ]
        assert leftovers == []

    def test_archive_replace_crash_leaves_registry_untouched(self, tmp_path, monkeypatch):
        """Crash the archive file's os.replace specifically (before the
        registry is ever rewritten) — proves step order: since the archive
        write happens first, a crash there must leave BOTH the archive
        (never created) and the live registry (untouched) exactly as
        they were before apply() started."""
        fx = build_fixture(tmp_path)
        original_registry_bytes = fx["registry_path"].read_bytes()
        report = audit(fx["registry_path"], [fx["agents_dir"]])

        archive_yaml = fx["registry_dir"] / "archive" / registry_gc.ARCHIVE_YAML_NAME
        real_replace = os.replace

        def boom(src, dst, *a, **kw):
            if str(archive_yaml) == str(dst):
                raise OSError("simulated crash during archive replace")
            return real_replace(src, dst, *a, **kw)

        monkeypatch.setattr(registry_gc.os, "replace", boom)

        with pytest.raises(OSError):
            apply(report, fx["registry_path"], fx["archive_dir"])

        assert not archive_yaml.exists()
        assert fx["registry_path"].read_bytes() == original_registry_bytes

    def test_apply_preserves_registry_file_mode(self, tmp_path):
        """End-to-end: an apply() that actually rewrites registry.yaml
        must not silently downgrade its permissions to mkstemp's 0600."""
        fx = build_fixture(tmp_path)
        os.chmod(fx["registry_path"], 0o644)
        report = audit(fx["registry_path"], [fx["agents_dir"]])

        apply(report, fx["registry_path"], fx["archive_dir"])

        mode = stat.S_IMODE(fx["registry_path"].stat().st_mode)
        assert mode == 0o644


class TestAtomicWriteModePreservation:
    """Low finding from re-review: tempfile.mkstemp always creates its
    temp file 0600, so a naive tmp+os.replace silently downgrades an
    existing more-permissive target (e.g. 0644) on every rewrite. Unit
    tests directly against _atomic_write_text, the lowest-level place the
    behavior lives."""

    def test_rewriting_existing_file_preserves_its_mode(self, tmp_path):
        target = tmp_path / "existing.yaml"
        target.write_text("a: 1\n", encoding="utf-8")
        os.chmod(target, 0o644)

        registry_gc._atomic_write_text(target, "a: 2\n")

        assert stat.S_IMODE(target.stat().st_mode) == 0o644
        assert target.read_text(encoding="utf-8") == "a: 2\n"

    def test_fresh_file_gets_mkstemp_default_mode(self, tmp_path):
        target = tmp_path / "brand-new.yaml"
        assert not target.exists()

        registry_gc._atomic_write_text(target, "a: 1\n")

        assert stat.S_IMODE(target.stat().st_mode) == 0o600
        assert target.read_text(encoding="utf-8") == "a: 1\n"


# ---------------------------------------------------------------------------
# Critical 3: registry header preservation
# ---------------------------------------------------------------------------


class TestHeaderPreservation:
    def test_registry_header_preserved_after_apply(self, tmp_path):
        fx = build_fixture_with_header(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        apply(report, fx["registry_path"], fx["archive_dir"])

        after_lines = fx["registry_path"].read_text(encoding="utf-8").splitlines()
        assert after_lines[: len(HEADER_COMMENT_LINES)] == HEADER_COMMENT_LINES

        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        for name in DEPRECATED_NAMES + ["orphaned-agent"]:
            assert name not in reloaded["agents"]
        assert "valid-agent" in reloaded["agents"]

    def test_header_untouched_when_registry_not_rewritten(self, tmp_path):
        """If deprecated/orphaned are both empty (e.g. a bak-only apply),
        the registry file is never opened for writing at all — so the
        header (and everything else) stays byte-identical, not just the
        header lines."""
        fx = build_fixture_with_header(tmp_path)
        # remove the deprecated/orphaned entries up front so only .bak is
        # actionable this run
        text = fx["registry_path"].read_text(encoding="utf-8")
        data = yaml.safe_load(
            "\n".join(text.splitlines()[len(HEADER_COMMENT_LINES) + 1 :])
        )
        for name in DEPRECATED_NAMES + ["orphaned-agent"]:
            data["agents"].pop(name, None)
        header_text = "\n".join(HEADER_COMMENT_LINES) + "\n\n"
        fx["registry_path"].write_text(
            header_text + yaml.dump(data, sort_keys=False), encoding="utf-8"
        )
        before_bytes = fx["registry_path"].read_bytes()

        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report.deprecated == []
        assert report.orphaned_entries == []
        assert len(report.bak_files) == 2

        apply(report, fx["registry_path"], fx["archive_dir"])

        assert fx["registry_path"].read_bytes() == before_bytes


# ---------------------------------------------------------------------------
# Important 1: duplicate top-level keys
# ---------------------------------------------------------------------------


class TestDuplicateTopLevelKeys:
    def test_audit_reports_duplicate_keys(self, tmp_path):
        fx = build_duplicate_key_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert "agents" in report.duplicate_keys

    def test_check_flags_duplicate_keys_as_actionable(self, tmp_path, capsys):
        fx = build_duplicate_key_fixture(tmp_path)
        rc = main(
            [
                "--registry",
                str(fx["registry_path"]),
                "--agents-dir",
                str(fx["agents_dir"]),
                "--check",
            ]
        )
        assert rc == 1

    def test_apply_aborts_cleanly_on_duplicate_keys_no_writes(self, tmp_path):
        fx = build_duplicate_key_fixture(tmp_path)
        before = snapshot_tree(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert "agents" in report.duplicate_keys

        with pytest.raises(DuplicateKeyError):
            apply(report, fx["registry_path"], fx["archive_dir"])

        after = snapshot_tree(tmp_path)
        assert before == after

    def test_no_duplicate_keys_on_clean_fixture(self, tmp_path):
        fx = build_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report.duplicate_keys == []

    def test_check_exits_1_from_duplicate_keys_alone(self, tmp_path, capsys):
        """Isolates duplicate_keys as the SOLE actionable reason — an
        otherwise fully clean registry (no deprecated/orphaned/.bak) with
        one duplicated top-level key must still fail --check."""
        fx = build_clean_fixture(tmp_path)
        text = fx["registry_path"].read_text(encoding="utf-8")
        # PyYAML keeps only the LAST "agents:" block (silent last-wins) —
        # reuse the same key with a source that still resolves, so the
        # surviving parsed data stays fully clean (no new orphan) and only
        # duplicate_keys is actionable.
        text += (
            '\nagents:\n  valid-agent:\n'
            '    description: "duplicate top-level agents block, still valid"\n'
            '    version: "1.0.0"\n'
            '    source: ".datacore/agents/valid-agent.md"\n'
        )
        fx["registry_path"].write_text(text, encoding="utf-8")

        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert report.deprecated == []
        assert report.orphaned_entries == []
        assert report.bak_files == []
        assert "agents" in report.duplicate_keys

        rc = main(
            [
                "--registry",
                str(fx["registry_path"]),
                "--agents-dir",
                str(fx["agents_dir"]),
                "--check",
            ]
        )
        assert rc == 1


# ---------------------------------------------------------------------------
# Controller Scope Amendment: module_agents: section coverage
# ---------------------------------------------------------------------------


class TestModuleAgentsSection:
    """The real registry carries agent entries under BOTH `agents:` and
    `module_agents:` (e.g. the 22 evaluator-* personas from Task 7.2 live
    under module_agents:). The original implementation only ever read
    `agents:` — these tests exercise both sections together end to end."""

    def test_audit_finds_deprecated_and_orphaned_in_both_sections(self, tmp_path):
        fx = build_dual_section_fixture(tmp_path)
        report = audit(
            fx["registry_path"], [fx["core_agents_dir"], fx["module_agents_dir"]]
        )

        assert sorted(report.deprecated) == sorted(
            ["agents/core-deprecated-agent", "module_agents/mod-deprecated-agent"]
        )
        assert sorted(report.orphaned_entries) == sorted(
            ["agents/core-orphaned-agent", "module_agents/mod-orphaned-agent"]
        )
        # one valid entry per section
        assert report.active_count == 2

    def test_apply_archives_both_sections_under_matching_keys(self, tmp_path):
        fx = build_dual_section_fixture(tmp_path)
        report = audit(
            fx["registry_path"], [fx["core_agents_dir"], fx["module_agents_dir"]]
        )

        apply(report, fx["registry_path"], fx["archive_dir"])

        archive_yaml = fx["registry_dir"] / "archive" / registry_gc.ARCHIVE_YAML_NAME
        assert archive_yaml.exists()
        archived = yaml.safe_load(archive_yaml.read_text(encoding="utf-8"))

        # each deprecated entry archived under its OWN section's key —
        # never flattened together, never cross-contaminated
        assert "core-deprecated-agent" in archived["agents"]
        assert "mod-deprecated-agent" in archived["module_agents"]
        assert "mod-deprecated-agent" not in archived.get("agents", {})
        assert "core-deprecated-agent" not in archived.get("module_agents", {})

        # def files moved into archive_dir for BOTH sections
        assert (fx["archive_dir"] / "core-deprecated-agent.md").exists()
        assert (fx["archive_dir"] / "mod-deprecated-agent.md").exists()
        assert not (fx["core_agents_dir"] / "core-deprecated-agent.md").exists()
        assert not (fx["module_agents_dir"] / "mod-deprecated-agent.md").exists()
        # valid entries' def files untouched
        assert (fx["core_agents_dir"] / "core-valid-agent.md").exists()
        assert (fx["module_agents_dir"] / "mod-valid-agent.md").exists()

        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        assert "core-deprecated-agent" not in reloaded["agents"]
        assert "core-orphaned-agent" not in reloaded["agents"]
        assert "core-valid-agent" in reloaded["agents"]
        assert "mod-deprecated-agent" not in reloaded["module_agents"]
        assert "mod-orphaned-agent" not in reloaded["module_agents"]
        assert "mod-valid-agent" in reloaded["module_agents"]

    def test_second_apply_is_noop_across_both_sections(self, tmp_path):
        fx = build_dual_section_fixture(tmp_path)
        report = audit(
            fx["registry_path"], [fx["core_agents_dir"], fx["module_agents_dir"]]
        )
        apply(report, fx["registry_path"], fx["archive_dir"])

        after_first = snapshot_tree(tmp_path)

        report2 = audit(
            fx["registry_path"], [fx["core_agents_dir"], fx["module_agents_dir"]]
        )
        assert report2.deprecated == []
        assert report2.orphaned_entries == []

        actions2 = apply(report2, fx["registry_path"], fx["archive_dir"])
        assert actions2 == []

        after_second = snapshot_tree(tmp_path)
        assert after_first == after_second


# ---------------------------------------------------------------------------
# Controller Scope Amendment: real-registry read-only verification
#
# Read-only against the REAL repo registry — no fixtures, no tmp_path
# mutation, NO apply() anywhere in this class. Confirms the module_agents
# blind spot (Task 7.2's structural find) is actually closed against real
# data, not just fixtures.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[3]
REAL_REGISTRY_PATH = REPO_ROOT / ".datacore" / "registry" / "agents.yaml"
REAL_ARCHIVE_PATH = (
    REPO_ROOT / ".datacore" / "registry" / "archive" / "agents-deprecated.yaml"
)


class TestRealRegistryModuleAgentsVisibility:
    """POST-APPLY STATE (updated by Task 7.3 — see its staleness-warning
    predecessor in git history / task-7.1-report.md "Extension" section for
    the original pre-apply framing).

    Task 7.3 ran `registry_gc.py --apply` for real against
    `.datacore/registry/agents.yaml` on 2026-07-30, archiving 28 deprecated
    entries (1 `agents/youtube-transcriber` + 27 `module_agents/*`,
    including all 22 `evaluator-*` personas) into
    `.datacore/registry/archive/agents-deprecated.yaml`. This class now
    asserts the POST-apply state instead of the pre-apply "still visible in
    the live registry" state the original (pre-7.3) version asserted:

    - the live registry has ZERO `module_agents/evaluator-*` entries left
      (deprecated or otherwise — they were removed from `module_agents:`
      entirely, not merely reclassified); and
    - the archive file has the 22 evaluator-* entries (plus the other 5
      pre-existing `module_agents:` deprecations) under its own
      `module_agents:` key, same spot-check personas as before.

    Still read-only (no `apply()` call anywhere in this class) — it reads
    the real, already-mutated files on disk, it does not mutate them.
    """

    def test_real_registry_module_agents_evaluator_entries_gone_live(self):
        assert REAL_REGISTRY_PATH.exists(), f"real registry not found at {REAL_REGISTRY_PATH}"

        report = audit(REAL_REGISTRY_PATH, [])  # read-only — no apply() call exists in this class

        # post-apply: no evaluator-* deprecations remain to be found in the
        # live registry at all (they were archived out, not just reclassified)
        module_agents_deprecated_evaluators = [
            d
            for d in report.deprecated
            if d.startswith("module_agents/evaluator-")
        ]
        assert module_agents_deprecated_evaluators == []

        # confirm they're gone from the raw live YAML entirely, not just
        # absent from the deprecated classification
        raw = yaml.safe_load(REAL_REGISTRY_PATH.read_text(encoding="utf-8"))
        live_module_agents = raw.get("module_agents") or {}
        live_evaluator_keys = [
            k for k in live_module_agents if k.startswith("evaluator-")
        ]
        assert live_evaluator_keys == []

    def test_real_archive_has_evaluator_entries_post_apply(self):
        assert REAL_ARCHIVE_PATH.exists(), (
            f"archive file not found at {REAL_ARCHIVE_PATH} — expected after "
            "Task 7.3's real --apply run"
        )

        archived = yaml.safe_load(REAL_ARCHIVE_PATH.read_text(encoding="utf-8")) or {}
        archived_module_agents = archived.get("module_agents") or {}

        archived_evaluators = [
            k for k in archived_module_agents if k.startswith("evaluator-")
        ]
        assert len(archived_evaluators) >= 22

        # spot-check the same handful of known evaluator personas are
        # actually present in the archive, under module_agents, not just a
        # raw count
        for persona in ("evaluator-critic", "evaluator-ceo", "evaluator-user"):
            assert persona in archived_module_agents


# ---------------------------------------------------------------------------
# Controller Scope Amendment: nested (per-section) duplicate-key pre-flight
# ---------------------------------------------------------------------------


CANONICAL_DEPRECATION_YAML = """\
version: "2.0.0"
protocol: "DIP-0016"
agents:
  active-agent:
    description: "Still active, untouched by any deprecation classification"
    version: "1.0.0"
    source: ".datacore/agents/active-agent.md"
  canonical-deprecated-agent:
    description: "Deprecated via the canonical DIP-0021 fields only -- no legacy marker, no status field"
    version: "1.0.0"
    source: ".datacore/agents/canonical-deprecated-agent.md"
    deprecated: true
    superseded_by: "active-agent"
  status-alias-deprecated-agent:
    description: "Deprecated via the v2 status: deprecated legacy alias"
    version: "1.0.0"
    source: ".datacore/agents/status-alias-deprecated-agent.md"
    status: deprecated
  marker-alias-deprecated-agent:
    description: "[DEPRECATED] via the legacy description marker alias"
    version: "1.0.0"
    source: ".datacore/agents/marker-alias-deprecated-agent.md"
  not-deprecated-explicit-false:
    description: "Explicit deprecated: false must NOT be classified deprecated"
    version: "1.0.0"
    source: ".datacore/agents/not-deprecated-explicit-false.md"
    deprecated: false
"""


def build_canonical_deprecation_fixture(root: Path) -> dict:
    """Fixture exercising all THREE accepted deprecation spellings side by
    side (DIP-0021 canonical `deprecated: true` + `superseded_by`, plus the
    two accepted legacy aliases: `status: deprecated` and the `[DEPRECATED]`
    description marker) -- and one explicit `deprecated: false` entry as a
    negative control. `canonical-deprecated-agent` is the actual gap this
    fixture locks in: it carries ONLY `deprecated: true` + `superseded_by`,
    no legacy marker and no status field -- matching real entries added
    under the DIP-0021 convention (RULINGS.md R4, dip-review/inspection)."""
    registry_dir = root / ".datacore" / "registry"
    agents_dir = root / ".datacore" / "agents"
    registry_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    registry_path = registry_dir / "agents.yaml"
    registry_path.write_text(CANONICAL_DEPRECATION_YAML, encoding="utf-8")
    for name in (
        "active-agent",
        "canonical-deprecated-agent",
        "status-alias-deprecated-agent",
        "marker-alias-deprecated-agent",
        "not-deprecated-explicit-false",
    ):
        (agents_dir / f"{name}.md").write_text(f"# {name}\n", encoding="utf-8")
    return {
        "root": root,
        "registry_path": registry_path,
        "registry_dir": registry_dir,
        "agents_dir": agents_dir,
        "archive_dir": root / ".datacore" / "agents" / "_deprecated",
    }


class TestCanonicalDeprecationConvention:
    """DIP-0021 established `deprecated: true` + `superseded_by` as the
    canonical registry deprecation convention (RULINGS.md R4). `status:
    deprecated` (v2 alias) and the `[DEPRECATED]` description marker
    (legacy alias) must both keep working unchanged. All three spellings
    must classify an entry as deprecated identically, and `superseded_by`
    -- provenance for WHY an entry was deprecated -- must survive into the
    archived copy."""

    def test_all_three_spellings_classified_deprecated(self, tmp_path):
        fx = build_canonical_deprecation_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])

        assert "agents/canonical-deprecated-agent" in report.deprecated
        assert "agents/status-alias-deprecated-agent" in report.deprecated
        assert "agents/marker-alias-deprecated-agent" in report.deprecated
        # negative controls: neither of these should be classified deprecated
        assert "agents/not-deprecated-explicit-false" not in report.deprecated
        assert "agents/active-agent" not in report.deprecated
        # active-agent + not-deprecated-explicit-false are the only survivors
        assert report.active_count == 2

    def test_superseded_by_preserved_in_archive(self, tmp_path):
        fx = build_canonical_deprecation_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert "agents/canonical-deprecated-agent" in report.deprecated

        apply(report, fx["registry_path"], fx["archive_dir"])

        archive_yaml = fx["registry_dir"] / "archive" / registry_gc.ARCHIVE_YAML_NAME
        archived = yaml.safe_load(archive_yaml.read_text(encoding="utf-8"))
        entry = archived["agents"]["canonical-deprecated-agent"]
        assert entry["superseded_by"] == "active-agent"
        assert entry["deprecated"] is True

        # it's also gone from the live registry, like any other archived entry
        reloaded = yaml.safe_load(fx["registry_path"].read_text(encoding="utf-8"))
        assert "canonical-deprecated-agent" not in reloaded["agents"]


class TestNestedDuplicateKeys:
    def test_audit_reports_nested_duplicate_with_section_prefix(self, tmp_path):
        fx = build_nested_duplicate_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert "module_agents/evaluator-critic" in report.duplicate_keys

    def test_apply_aborts_cleanly_on_nested_duplicate_no_writes(self, tmp_path):
        fx = build_nested_duplicate_fixture(tmp_path)
        before = snapshot_tree(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        assert "module_agents/evaluator-critic" in report.duplicate_keys

        with pytest.raises(DuplicateKeyError):
            apply(report, fx["registry_path"], fx["archive_dir"])

        after = snapshot_tree(tmp_path)
        assert before == after

    def test_check_exits_1_from_nested_duplicate_alone(self, tmp_path, capsys):
        fx = build_nested_duplicate_fixture(tmp_path)
        rc = main(
            [
                "--registry",
                str(fx["registry_path"]),
                "--agents-dir",
                str(fx["agents_dir"]),
                "--check",
            ]
        )
        assert rc == 1

    def test_duplicate_top_level_key_fixture_has_no_nested_dupe(self, tmp_path):
        """Regression guard: the (unrelated) top-level-duplicate fixture
        from the prior review pass must NOT also trip the new nested-dupe
        scan — its appended second `agents:` block has exactly one entry,
        no repeated name within it."""
        fx = build_duplicate_key_fixture(tmp_path)
        report = audit(fx["registry_path"], [fx["agents_dir"]])
        nested = [d for d in report.duplicate_keys if "/" in d]
        assert nested == []

