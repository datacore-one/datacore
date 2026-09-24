"""The public root context layer must read correctly in any agent harness.

AGENTS.md and GEMINI.md are composed from the same layers as CLAUDE.md, so
advice naming only Claude Code's tools misleads Codex, Cursor and the rest.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_root_base_layer_names_the_portable_command_path():
    text = (ROOT / "CLAUDE.base.md").read_text()
    assert "datacore_command_run" in text
    assert "(any harness)" in text


def test_root_base_layer_does_not_prescribe_claude_only_tools():
    text = (ROOT / "CLAUDE.base.md").read_text()
    for claude_only in ("`Glob` not `find`", "`Grep` not `grep`"):
        assert claude_only not in text, claude_only
