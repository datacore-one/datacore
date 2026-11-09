"""MOD-1: Installing a module makes exactly the commands, agents and tools it declares
available, and nothing it does not declare.

Kind: deterministic. A module is installed into a tmp installation and the real
datacore MCP server (the surface the AI uses: tools/list, datacore_command_list,
datacore_agent_list) is asked what is available.

The module declares one tool, one command and one agent in module.yaml and ships,
besides those, one undeclared tool, command and agent.

Seeded failure: a declared command or agent is not reachable (datacore_command_list
and datacore_agent_list read only .datacore/commands and .datacore/agents, never a
module's); or an undeclared tool/command/agent the module happens to ship is
exposed.
"""
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_client import McpClient  # noqa: E402

MCP = ROOT / "2-datacore" / "2-projects" / "datacore-mcp"
SERVER = MCP / "dist" / "index.js"

MANIFEST = """name: evalmod
version: 0.1.0
description: promise eval fixture
provides:
  tools:
    - name: declared_tool
      description: declared
      handler: tools/index.js
  commands:
    - evalmod-report
  agents:
    - evalmod-agent
"""

TOOLS_JS = """import {{ z }} from '{runtime}'
const tool = (name) => ({{ name, description: name, inputSchema: z.object({{}}),
  handler: async () => ({{ ok: name }}) }})
export const tools = [tool('declared_tool'), tool('undeclared_tool')]
"""

DOC = "---\nname: {n}\ndescription: {n}\n---\n\n# {n}\n\nDo the thing.\n"


def _install(tmp_path):
    root = tmp_path / "Data"
    for rel in (".datacore/commands", ".datacore/agents", "0-personal/.datacore", "0-personal/notes/journals",
                "0-personal/3-knowledge"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "0-personal/.datacore/config.yaml").write_text("space:\n  name: personal\n  type: personal\n  owner: owner\n")
    (root / ".datacore/commands/core-cmd.md").write_text(DOC.format(n="core-cmd"))
    (root / ".datacore/agents/core-agent.md").write_text(DOC.format(n="core-agent"))
    mod = root / ".datacore/modules/evalmod"
    for sub in ("tools", "commands", "agents"):
        (mod / sub).mkdir(parents=True)
    (mod / "module.yaml").write_text(MANIFEST)
    (mod / "tools/index.js").write_text(TOOLS_JS.format(runtime=(MCP / "dist" / "runtime.js").as_uri()))
    for n in ("evalmod-report", "evalmod-undeclared"):
        (mod / "commands" / f"{n}.md").write_text(DOC.format(n=n))
    for n in ("evalmod-agent", "evalmod-rogue"):
        (mod / "agents" / f"{n}.md").write_text(DOC.format(n=n))
    return root.resolve()


@pytest.fixture
def server(tmp_path):
    node = shutil.which("node") or str(Path.home() / ".nvm/versions/node/v24.10.0/bin/node")
    assert Path(node).exists() and SERVER.is_file(), f"server not installed: {SERVER}"
    root = _install(tmp_path)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "DATACORE_PATH": str(root),
           "DATACORE_LIB": str(LIB), "DATACORE_PYTHON": sys.executable}
    c = McpClient([node, str(SERVER)], env=env, cwd=str(tmp_path))
    c.initialize(timeout=30)
    yield c
    c.close()


def _names(resp):
    text = "".join(p.get("text", "") for p in resp["result"].get("content", []))
    return text


def test_exactly_the_declared_tools_are_exposed(server):
    names = {t["name"] for t in server.request("tools/list")["result"]["tools"]}
    assert "datacore_evalmod_declared_tool" in names, sorted(n for n in names if "evalmod" in n)
    assert not [n for n in names if "undeclared_tool" in n], "an undeclared module tool is exposed"


def test_exactly_the_declared_commands_are_available(server):
    listing = _names(server.call("datacore_command_list", {}))
    assert "core-cmd" in listing, f"command_list is not working at all: {listing[:300]}"
    assert "evalmod-undeclared" not in listing, "an undeclared module command is available"
    assert "evalmod-report" in listing, (
        f"the module's declared command 'evalmod-report' is not available; command_list said: {listing[:400]}")


def test_exactly_the_declared_agents_are_available(server):
    listing = _names(server.call("datacore_agent_list", {}))
    assert "core-agent" in listing, f"agent_list is not working at all: {listing[:300]}"
    assert "evalmod-rogue" not in listing, "an undeclared module agent is available"
    assert "evalmod-agent" in listing, (
        f"the module's declared agent 'evalmod-agent' is not available; agent_list said: {listing[:400]}")
