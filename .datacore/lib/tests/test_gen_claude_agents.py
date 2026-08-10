"""Tests for gen_claude_agents.py -- .claude artifact generator from the
agent registry (DIP-0016).

Datacore v2 Phase 8, Task 8.2. Harness artifacts (.claude/agents/*.md)
become generated outputs of the registry, never hand-authored -- the
anti-lock-in one-way rule. Everything here runs against tmp_path fixtures
mirroring the real DIP-0016 registry shape (a top-level mapping keyed by
agent name, under `agents:` and `module_agents:`, each entry carrying at
minimum description/source, per registry_gc.py's own documented shape) --
the real ~/Data/.claude directory is never touched by this suite (that's
task 8.3, gated on whether .claude/agents exists in-repo).
"""

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))
import gen_claude_agents  # noqa: E402
from gen_claude_agents import (  # noqa: E402
    GENERATED_HEADER,
    GenError,
    GenReport,
    check,
    generate,
    main,
)


BASIC_REGISTRY = """\
version: "2.0.0"
protocol: "DIP-0016"
updated: "2026-07-29"

agents:
  zulu-agent:
    description: "Zulu does other things"
    version: "1.0.0"
    source: .datacore/agents/zulu-agent.md
    skills:
    - testing
  alpha-agent:
    description: "Alpha does things"
    version: "1.0.0"
    source: .datacore/agents/alpha-agent.md
    skills:
    - testing
  deprecated-agent:
    description: "An agent no longer used"
    version: "1.0.0"
    source: .datacore/agents/deprecated-agent.md
    status: deprecated

module_agents:
  mod-agent:
    description: "Module contributed agent"
    version: "1.0.0"
    source: .datacore/modules/foo/agents/mod-agent.md
"""

COLLISION_REGISTRY = """\
version: "2.0.0"
protocol: "DIP-0016"
updated: "2026-07-29"

agents:
  shared-agent:
    description: "Lives in agents section"
    version: "1.0.0"
    source: .datacore/agents/shared-agent.md

module_agents:
  shared-agent:
    description: "Lives in module_agents section too"
    version: "1.0.0"
    source: .datacore/modules/foo/agents/shared-agent.md
"""

TRICKY_DESC_REGISTRY = """\
version: "2.0.0"
protocol: "DIP-0016"
updated: "2026-07-29"

agents:
  tricky-agent:
    description: |
      Handles "quoted" text, colons: like this, and
      multiple lines of prose.
    version: "1.0.0"
    source: .datacore/agents/tricky-agent.md
"""

# A description whose own content contains a bare "---" line -- the exact
# character sequence used as the frontmatter delimiter. If the renderer
# ever fell back to naive string interpolation instead of a real YAML
# dumper, this would produce a *third*, bare, column-0 "---" line and
# corrupt the frontmatter block for any consumer that splits on it.
EMBEDDED_DELIMITER_REGISTRY = """\
version: "2.0.0"
protocol: "DIP-0016"
updated: "2026-07-29"

agents:
  delimiter-agent:
    description: |
      line one
      ---
      line three
    version: "1.0.0"
    source: .datacore/agents/delimiter-agent.md
"""


def write_registry(tmp_path: Path, text: str, name: str = "agents.yaml") -> Path:
    registry_dir = tmp_path / ".datacore" / "registry"
    registry_dir.mkdir(parents=True, exist_ok=True)
    path = registry_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def parse_generated(content: str):
    """Split a generated file's content into (header, frontmatter_dict, body)."""
    lines = content.splitlines()
    assert lines[0] == GENERATED_HEADER
    assert lines[1] == ""
    assert lines[2] == "---"
    end = lines.index("---", 3)
    frontmatter_text = "\n".join(lines[3:end])
    frontmatter = yaml.safe_load(frontmatter_text)
    body = "\n".join(lines[end + 1 :]).strip("\n")
    return lines[0], frontmatter, body


class TestGenerateShape:
    def test_header_frontmatter_body_shape(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))

        content = (out_dir / "alpha-agent.md").read_text(encoding="utf-8")
        header, frontmatter, body = parse_generated(content)

        assert header == "# GENERATED from .datacore/registry/agents.yaml — do not edit"
        assert frontmatter["name"] == "alpha-agent"
        assert frontmatter["description"] == "Alpha does things"
        assert ".datacore/agents/alpha-agent.md" in body

    def test_out_dir_created_if_missing(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "nested" / "does" / "not" / "exist"
        assert not out_dir.exists()
        generate(registry_path, out_dir, sections=("agents",))
        assert out_dir.exists()
        assert (out_dir / "alpha-agent.md").exists()

    def test_returns_gen_report_type(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        report = generate(registry_path, out_dir, sections=("agents",))
        assert isinstance(report, GenReport)
        assert isinstance(report.written, list)
        assert isinstance(report.skipped_deprecated, int)


class TestDeprecatedSkipped:
    def test_deprecated_entry_not_written(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        assert not (out_dir / "deprecated-agent.md").exists()

    def test_deprecated_count_reported(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        report = generate(registry_path, out_dir, sections=("agents",))
        assert report.skipped_deprecated == 1

    def test_active_entries_still_written_alongside_deprecated(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        report = generate(registry_path, out_dir, sections=("agents",))
        assert "alpha-agent.md" in report.written
        assert "zulu-agent.md" in report.written
        assert "deprecated-agent.md" not in report.written


class TestSortedDeterminism:
    def test_written_list_is_sorted_by_name(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        report = generate(registry_path, out_dir, sections=("agents",))
        assert report.written == sorted(report.written)
        assert report.written == ["alpha-agent.md", "zulu-agent.md"]


class TestIdempotent:
    def test_second_run_is_byte_identical(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        report1 = generate(registry_path, out_dir, sections=("agents",))
        contents1 = {
            f.name: f.read_bytes() for f in sorted(out_dir.glob("*.md"))
        }
        report2 = generate(registry_path, out_dir, sections=("agents",))
        contents2 = {
            f.name: f.read_bytes() for f in sorted(out_dir.glob("*.md"))
        }
        assert report1.written == report2.written
        assert contents1 == contents2


class TestMultiSection:
    def test_both_sections_generated_when_no_collision(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        report = generate(
            registry_path, out_dir, sections=("agents", "module_agents")
        )
        assert "mod-agent.md" in report.written
        assert "alpha-agent.md" in report.written
        content = (out_dir / "mod-agent.md").read_text(encoding="utf-8")
        _, frontmatter, body = parse_generated(content)
        assert frontmatter["name"] == "mod-agent"
        assert ".datacore/modules/foo/agents/mod-agent.md" in body

    def test_default_sections_is_agents_only(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        report = generate(registry_path, out_dir)
        assert "mod-agent.md" not in report.written


class TestCrossSectionCollision:
    def test_collision_raises_generror_naming_both(self, tmp_path):
        registry_path = write_registry(tmp_path, COLLISION_REGISTRY)
        out_dir = tmp_path / "out"
        with pytest.raises(GenError) as excinfo:
            generate(registry_path, out_dir, sections=("agents", "module_agents"))
        message = str(excinfo.value)
        assert "shared-agent" in message
        assert "agents" in message
        assert "module_agents" in message

    def test_collision_does_not_occur_when_only_one_section_requested(self, tmp_path):
        registry_path = write_registry(tmp_path, COLLISION_REGISTRY)
        out_dir = tmp_path / "out"
        report = generate(registry_path, out_dir, sections=("agents",))
        assert report.written == ["shared-agent.md"]


class TestFrontmatterYamlSafety:
    def test_quotes_colons_and_newlines_survive_round_trip(self, tmp_path):
        registry_path = write_registry(tmp_path, TRICKY_DESC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        content = (out_dir / "tricky-agent.md").read_text(encoding="utf-8")
        _, frontmatter, _ = parse_generated(content)
        original = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        expected_desc = original["agents"]["tricky-agent"]["description"]
        assert frontmatter["description"] == expected_desc
        assert '"quoted"' in expected_desc  # sanity: fixture actually has a quote
        assert ":" in expected_desc  # sanity: fixture actually has a colon
        assert "\n" in expected_desc  # sanity: fixture is actually multi-line

    def test_embedded_delimiter_line_does_not_produce_a_bare_column0_dashes(
        self, tmp_path
    ):
        registry_path = write_registry(tmp_path, EMBEDDED_DELIMITER_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        content = (out_dir / "delimiter-agent.md").read_text(encoding="utf-8")

        # Exactly two bare, column-0 "---" lines: the real frontmatter open
        # and close. The description's own embedded "---" must never appear
        # as a third bare delimiter line (it must be indented/quoted).
        bare_dashes = [line for line in content.splitlines() if line == "---"]
        assert len(bare_dashes) == 2

        _, frontmatter, _ = parse_generated(content)
        original = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
        assert (
            frontmatter["description"]
            == original["agents"]["delimiter-agent"]["description"]
        )


class TestCheck:
    def test_check_clean_after_fresh_generate(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        result = check(registry_path, out_dir, sections=("agents",))
        assert result.clean is True
        assert result.drift_names() == []

    def test_check_detects_content_drift(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        (out_dir / "alpha-agent.md").write_text("hand-edited garbage\n", encoding="utf-8")
        result = check(registry_path, out_dir, sections=("agents",))
        assert result.clean is False
        assert "alpha-agent" in result.drifted
        assert "alpha-agent" in result.drift_names()

    def test_check_detects_missing_file(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        (out_dir / "alpha-agent.md").unlink()
        result = check(registry_path, out_dir, sections=("agents",))
        assert result.clean is False
        assert "alpha-agent" in result.missing
        assert "alpha-agent" in result.drift_names()

    def test_check_detects_extra_file(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        (out_dir / "not-in-registry.md").write_text("stray file\n", encoding="utf-8")
        result = check(registry_path, out_dir, sections=("agents",))
        assert result.clean is False
        assert "not-in-registry" in result.extra
        assert "not-in-registry" in result.drift_names()

    def test_check_never_writes_files(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        check(registry_path, out_dir, sections=("agents",))
        assert not out_dir.exists()

    def test_check_reports_skipped_deprecated_too(self, tmp_path):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        result = check(registry_path, out_dir, sections=("agents",))
        assert result.skipped_deprecated == 1


class TestCli:
    def test_generate_mode_writes_files_and_exits_0(self, tmp_path, capsys):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        rc = main(
            ["--registry", str(registry_path), "--out", str(out_dir)]
        )
        assert rc == 0
        assert (out_dir / "alpha-agent.md").exists()

    def test_check_mode_exits_0_when_clean(self, tmp_path, capsys):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        rc = main(
            ["--registry", str(registry_path), "--out", str(out_dir), "--check"]
        )
        assert rc == 0

    def test_check_mode_exits_1_and_lists_drift(self, tmp_path, capsys):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        generate(registry_path, out_dir, sections=("agents",))
        (out_dir / "alpha-agent.md").write_text("changed\n", encoding="utf-8")
        rc = main(
            ["--registry", str(registry_path), "--out", str(out_dir), "--check"]
        )
        assert rc == 1
        captured = capsys.readouterr()
        assert "alpha-agent" in captured.out

    def test_multi_section_flag_parses_comma_list(self, tmp_path, capsys):
        registry_path = write_registry(tmp_path, BASIC_REGISTRY)
        out_dir = tmp_path / "out"
        rc = main(
            [
                "--registry",
                str(registry_path),
                "--out",
                str(out_dir),
                "--sections",
                "agents,module_agents",
            ]
        )
        assert rc == 0
        assert (out_dir / "mod-agent.md").exists()

    def test_collision_via_cli_returns_nonzero(self, tmp_path, capsys):
        registry_path = write_registry(tmp_path, COLLISION_REGISTRY)
        out_dir = tmp_path / "out"
        rc = main(
            [
                "--registry",
                str(registry_path),
                "--out",
                str(out_dir),
                "--sections",
                "agents,module_agents",
            ]
        )
        assert rc != 0


class TestModuleExports:
    def test_gen_error_is_exception_subclass(self):
        assert issubclass(GenError, Exception)

    def test_module_has_expected_public_names(self):
        for name in ("generate", "check", "GenReport", "GenError", "main", "GENERATED_HEADER"):
            assert hasattr(gen_claude_agents, name)
