"""Focus context must reach every harness, not only Claude Code.

Codex, Cursor, Antigravity, OpenCode and OpenClaw read AGENTS.md; only Claude
Code reads CLAUDE.md. A project that got the Datacore section in CLAUDE.md
alone was invisible to all of them.
"""
import apply_focus_context as afc


def _project(tmp_path, files=None):
    root = tmp_path
    (root / ".datacore").mkdir()
    proj = root / "1-team" / "2-projects" / "app"
    proj.mkdir(parents=True)
    for name, text in (files or {}).items():
        (proj / name).write_text(text)
    return root, proj


def _apply(root):
    for p in afc.find_projects(root):
        afc.apply_to_project(p, dry_run=False)


def test_section_lands_in_agents_md_as_well(tmp_path):
    root, proj = _project(tmp_path, {"CLAUDE.md": "# App\n\nBuild with make.\n"})
    _apply(root)
    assert afc.MARKER in (proj / "CLAUDE.md").read_text()
    agents = (proj / "AGENTS.md").read_text()
    assert afc.MARKER in agents
    assert "CLAUDE.md" in agents          # points at the project's real guidance


def test_existing_agents_md_is_appended_not_replaced(tmp_path):
    root, proj = _project(tmp_path, {"AGENTS.md": "# Mine\n\nKeep this.\n"})
    _apply(root)
    text = (proj / "AGENTS.md").read_text()
    assert text.startswith("# Mine\n\nKeep this.")
    assert afc.MARKER in text


def test_rerun_is_idempotent(tmp_path):
    root, proj = _project(tmp_path, {"CLAUDE.md": "# App\n"})
    _apply(root)
    first = {n: (proj / n).read_text() for n in ("CLAUDE.md", "AGENTS.md")}
    _apply(root)
    assert {n: (proj / n).read_text() for n in first} == first


def test_section_tells_non_claude_harnesses_how_to_run_commands(tmp_path):
    section = afc.generate_section("1-team")
    assert "datacore_command_run" in section
