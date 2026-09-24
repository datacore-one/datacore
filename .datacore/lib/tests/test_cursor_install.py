"""Cursor adapter installer: wire Datacore (and PLUR) into <root>/.cursor/.

Merges by ownership: it replaces only the `datacore` / `plur` MCP entries and
the hook entries it wrote (fingerprinted by command), keeps everything else,
and refuses to overwrite a config it cannot parse.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "cursor_install", Path(__file__).resolve().parents[2] / "adapters" / "cursor" / "install.py")
ci = importlib.util.module_from_spec(SPEC)
sys.modules["cursor_install"] = ci   # @dataclass resolves its module by name
SPEC.loader.exec_module(ci)

ROOT = Path("/data")
TOOLS = ci.Tools(datacore_mcp="/bin/datacore-mcp", plur_mcp="/bin/plur-mcp",
                 plur_hook="/opt/plur/bin/plur-hook", python="/data/.datacore/venv/bin/python")


def test_mcp_entries_use_the_cursor_profile_and_explicit_paths():
    cfg = ci.merge_mcp({}, ROOT, TOOLS)
    dc = cfg["mcpServers"]["datacore"]
    assert dc["command"] == "/bin/datacore-mcp"
    assert dc["env"] == {"DATACORE_PATH": "/data", "DATACORE_PYTHON": "/data/.datacore/venv/bin/python",
                         "DATACORE_TOOL_PROFILE": "cursor"}
    assert cfg["mcpServers"]["plur"]["env"]["PLUR_TOOL_PROFILE"] == "cursor"


def test_mcp_merge_keeps_the_users_servers_and_keys():
    existing = {"mcpServers": {"MCP_DOCKER": {"command": "docker"}, "datacore": {"command": "old"}}, "x": 1}
    cfg = ci.merge_mcp(existing, ROOT, TOOLS)
    assert cfg["mcpServers"]["MCP_DOCKER"] == {"command": "docker"}
    assert cfg["x"] == 1
    assert cfg["mcpServers"]["datacore"]["command"] == "/bin/datacore-mcp"


def test_hooks_register_the_guard_bridge_and_plur_without_duplicates():
    cfg = ci.merge_hooks({}, ROOT, TOOLS)
    again = ci.merge_hooks(cfg, ROOT, TOOLS)
    assert again == cfg
    pre = [h["command"] for h in cfg["hooks"]["preToolUse"]]
    assert any("adapters/cursor/hook.py" in c for c in pre)
    assert any("plur-hook hook-cursor-guard" in c for c in pre)
    assert any("adapters/cursor/hook.py" in h["command"] for h in cfg["hooks"]["beforeShellExecution"])
    assert all(h["failClosed"] is False for hs in cfg["hooks"].values() for h in hs)
    assert cfg["version"] == 1


def test_hooks_merge_keeps_the_users_own_hooks():
    mine = {"command": "./scripts/lint.sh"}
    cfg = ci.merge_hooks({"version": 1, "hooks": {"preToolUse": [mine]}, "extra": True}, ROOT, TOOLS)
    assert mine in cfg["hooks"]["preToolUse"]
    assert cfg["extra"] is True


def test_without_plur_only_datacore_is_wired():
    tools = ci.Tools(datacore_mcp="/bin/datacore-mcp", plur_mcp=None, plur_hook=None, python="/usr/bin/python3")
    assert "plur" not in ci.merge_mcp({}, ROOT, tools)["mcpServers"]
    assert "sessionStart" not in ci.merge_hooks({}, ROOT, tools)["hooks"]


def test_install_refuses_to_overwrite_an_unparseable_config(tmp_path):
    (tmp_path / ".cursor").mkdir()
    bad = tmp_path / ".cursor" / "mcp.json"
    bad.write_text("{ not json")
    with pytest.raises(ci.InstallError):
        ci.install(tmp_path, TOOLS)
    assert bad.read_text() == "{ not json"


def test_install_writes_both_files(tmp_path):
    ci.install(tmp_path, TOOLS)
    assert json.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]["datacore"]
    assert json.loads((tmp_path / ".cursor" / "hooks.json").read_text())["hooks"]["preToolUse"]
