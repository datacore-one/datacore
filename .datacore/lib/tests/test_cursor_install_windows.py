"""Cursor adapter on Windows.

Found 2026-09-27 while tracing a Windows user's broken Cursor: the adapter was
written for macOS and Linux paths, so on Windows

- the hook command was `{python} {hook.py}` unquoted — any space in the user
  name or install path split it, and every guard call failed;
- re-running it duplicated its own hooks: ownership is recognised by the
  substring `adapters/cursor/hook.py`, and Windows writes backslashes;
- the venv interpreter was looked for at `venv/bin/python` (Windows:
  `venv\\Scripts\\python.exe`) and PLUR's hook shim at `plur-hook` (Windows:
  `plur-hook.cmd`, per plur's init.ts);
- the MCP servers were registered as npm `.cmd` shims instead of node running
  the package script, which is what the Datacore CLI writes on Windows.

Every function takes an explicit `windows` flag so this runs on any OS.
"""
import importlib.util
import json
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "cursor_install_w", Path(__file__).resolve().parents[2] / "adapters" / "cursor" / "install.py")
ci = importlib.util.module_from_spec(SPEC)
sys.modules["cursor_install_w"] = ci
SPEC.loader.exec_module(ci)

PY = r"C:\Users\Ana Maria\AppData\Local\Programs\Python\Python311\python.exe"
ROOT = r"C:\Users\Ana Maria\Data"


def _tools(**kw):
    base = dict(datacore_mcp=r"C:\npm\datacore-mcp.cmd", plur_mcp=None, plur_hook=None, python=PY)
    base.update(kw)
    return ci.Tools(**base)


def test_hook_command_quotes_paths_with_spaces():
    cmd = ci.hook_command(PY, ROOT + r"\.datacore\adapters\cursor\hook.py", windows=True)
    assert cmd == f'"{PY}" "{ROOT}\\.datacore\\adapters\\cursor\\hook.py"'


def test_hook_command_on_posix_is_shell_quoted():
    assert ci.hook_command("/usr/bin/python3", "/home/ana maria/Data/hook.py", windows=False) \
        == "/usr/bin/python3 '/home/ana maria/Data/hook.py'"


def test_merge_hooks_writes_quoted_commands_on_windows():
    cfg = ci.merge_hooks({}, ROOT, _tools(), windows=True)
    cmd = cfg["hooks"]["preToolUse"][0]["command"]
    assert cmd.startswith(f'"{PY}" ')


def test_rerun_on_windows_replaces_its_own_hooks_instead_of_duplicating():
    first = ci.merge_hooks({}, ROOT, _tools(), windows=True)
    again = ci.merge_hooks(json.loads(json.dumps(first)), ROOT, _tools(), windows=True)
    assert len(again["hooks"]["preToolUse"]) == 1
    assert len(again["hooks"]["beforeShellExecution"]) == 1


def test_plur_hooks_use_the_cmd_shim_quoted():
    shim = r"C:\Users\Ana Maria\.plur\bin\plur-hook.cmd"
    cfg = ci.merge_hooks({}, ROOT, _tools(plur_hook=shim), windows=True)
    assert cfg["hooks"]["sessionStart"][0]["command"] == f'"{shim}" hook-cursor-session-start'
    again = ci.merge_hooks(json.loads(json.dumps(cfg)), ROOT, _tools(plur_hook=shim), windows=True)
    assert len(again["hooks"]["preToolUse"]) == 2   # bridge + plur guard, once each


def test_resolve_tools_uses_windows_names(tmp_path):
    root, home = tmp_path / "Data", tmp_path / "home"
    venv = root / ".datacore" / "venv" / "Scripts" / "python.exe"
    shim = home / ".plur" / "bin" / "plur-hook.cmd"
    for f in (venv, shim):
        f.parent.mkdir(parents=True)
        f.write_text("")
    tools = ci.resolve_tools(root, windows=True, home=home, which=lambda _: None)
    assert tools.python == str(venv)
    assert tools.plur_hook == str(shim)


def test_mcp_entry_runs_node_on_the_package_script(tmp_path):
    prefix = tmp_path / "npm"
    pkg = prefix / "node_modules" / "@datacore-one" / "mcp"
    (pkg / "dist").mkdir(parents=True)
    (pkg / "dist" / "index.js").write_text("")
    (pkg / "package.json").write_text(json.dumps({"bin": {"datacore-mcp": "dist/index.js"}}))
    (prefix / "datacore-mcp.cmd").write_text("")
    entry = ci.mcp_entry(str(prefix / "datacore-mcp.cmd"), node=r"C:\node\node.exe", windows=True)
    assert entry == {"command": r"C:\node\node.exe", "args": [str(pkg / "dist" / "index.js")]}


def test_mcp_entry_off_windows_is_the_command_itself():
    assert ci.mcp_entry("/usr/local/bin/datacore-mcp", node="/usr/bin/node", windows=False) \
        == {"command": "/usr/local/bin/datacore-mcp"}
