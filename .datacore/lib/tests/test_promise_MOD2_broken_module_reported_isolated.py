"""MOD-2: A module that is broken or missing a requirement is reported when installed and
never breaks the core or other modules.

Kind: deterministic. Four modules are installed into a tmp installation next to a
healthy one; the real datacore MCP server starts on it; the core and the healthy
module are exercised and datacore_modules_health (the report surface) is read.

  broken  -- its tools/index.js throws on import
  needy   -- declares a dependency on a module that is not installed
  mangled -- its module.yaml is not valid YAML

Seeded failure: one broken module stops the server or hides the healthy module's
tools; or a broken module is skipped silently (an unparsable module.yaml is
dropped by scanModulesDir's bare `catch {}`; `dependencies:` are never checked).
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


def _manifest(name, extra=""):
    return (f"name: {name}\nversion: 0.1.0\nmanifest_version: 2\ndescription: eval\n"
            f"provides:\n  tools:\n    - name: ping\n      handler: tools/index.js\n{extra}")


def _tools(runtime, body="export const tools = [{ name: 'ping', description: 'ping', "
                         "inputSchema: z.object({}), handler: async () => ({ pong: true }) }]\n"):
    return f"import {{ z }} from '{runtime}'\n{body}"


def _install(tmp_path):
    root = tmp_path / "Data"
    for rel in (".datacore", "0-personal/.datacore", "0-personal/notes/journals", "0-personal/3-knowledge"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "0-personal/.datacore/config.yaml").write_text("space:\n  name: personal\n  type: personal\n  owner: owner\n")
    runtime = (MCP / "dist" / "runtime.js").as_uri()
    mods = {
        "good": (_manifest("good"), _tools(runtime)),
        "broken": (_manifest("broken"), _tools(runtime, "throw new Error('broken on import')\n")),
        "needy": (_manifest("needy", "dependencies:\n  - ghostdep@>=1.0.0\n"), _tools(runtime)),
        "mangled": ("name: mangled\nprovides: [unclosed\n  tools: {\n", _tools(runtime)),
    }
    for name, (manifest, js) in mods.items():
        d = root / ".datacore/modules" / name
        (d / "tools").mkdir(parents=True)
        (d / "module.yaml").write_text(manifest)
        (d / "tools/index.js").write_text(js)
        (d / "SKILL.md").write_text("# skill\n")
        (d / "CLAUDE.base.md").write_text("# ctx\n")
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


def _json(resp):
    text = "".join(p.get("text", "") for p in resp["result"].get("content", []))
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text}


def _health(server):
    rep = _json(server.call("datacore_modules_health", {}))
    return {m["name"]: m for m in rep.get("modules", [])}, json.dumps(rep)


def test_broken_modules_never_break_the_core_or_a_healthy_module(server):
    names = {t["name"] for t in server.request("tools/list")["result"]["tools"]}
    assert "datacore_date" in names and "datacore_capture" in names, "core tools missing"
    assert "datacore_good_ping" in names, "a healthy module lost its tool because another module is broken"
    out = _json(server.call("datacore_good_ping", {}))
    assert "pong" in json.dumps(out), out
    assert "result" in server.call("datacore_date", {"op": "today"}), "the core stopped answering"


def test_a_module_that_fails_to_load_is_reported(server):
    health, raw = _health(server)
    assert "broken" in health and health["broken"]["status"] == "error", raw[:600]


def test_a_module_missing_a_required_module_is_reported(server):
    health, raw = _health(server)
    needy = health.get("needy") or {}
    assert needy.get("status") in ("error", "warning") and "ghostdep" in json.dumps(needy), (
        f"'needy' requires ghostdep, which is not installed, and the health report does not say so: {needy}")


def test_a_module_with_an_unreadable_manifest_is_reported(server):
    health, raw = _health(server)
    assert "mangled" in raw, (
        "a module whose module.yaml does not parse is dropped silently; the health report "
        f"names only {sorted(health)}")
