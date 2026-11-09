"""MOD-3: Removing or disabling a module removes everything it added, including its
schedules and tools.

Kind: deterministic (tools: a module is installed into a tmp installation, the real
datacore MCP server lists its tool, the module is removed, the server restarts)
plus a contract on the real declared schedule list (lib/jobs/manifest.yaml): a
schedule can leave with its module only if the module owns it. Today schedules
that run module code are declared centrally, and no module.yaml declares any, so
removing the module leaves every one of them behind, firing into a missing path.
There is also no disable operation at all (no `enabled` switch the loader honours).

Seeded failure: the removed module's tool is still listed after restart; or a job
running .datacore/modules/<m>/... code is declared outside module <m>.
"""
import os
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_client import McpClient  # noqa: E402

MCP = ROOT / "2-datacore" / "2-projects" / "datacore-mcp"
SERVER = MCP / "dist" / "index.js"


def _start(tmp_path, root):
    node = shutil.which("node") or str(Path.home() / ".nvm/versions/node/v24.10.0/bin/node")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "DATACORE_PATH": str(root),
           "DATACORE_LIB": str(LIB), "DATACORE_PYTHON": sys.executable}
    c = McpClient([node, str(SERVER)], env=env, cwd=str(tmp_path))
    c.initialize(timeout=30)
    return c


def test_removing_a_module_removes_its_tools(tmp_path):
    assert SERVER.is_file(), SERVER
    root = tmp_path / "Data"
    for rel in (".datacore", "0-personal/.datacore", "0-personal/notes/journals", "0-personal/3-knowledge"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    (root / "0-personal/.datacore/config.yaml").write_text("space:\n  name: personal\n  type: personal\n  owner: owner\n")
    mod = root / ".datacore/modules/leaving"
    (mod / "tools").mkdir(parents=True)
    (mod / "module.yaml").write_text("name: leaving\nversion: 0.1.0\nprovides:\n  tools:\n"
                                     "    - name: wave\n      handler: tools/index.js\n")
    (mod / "tools/index.js").write_text(
        f"import {{ z }} from '{(MCP / 'dist' / 'runtime.js').as_uri()}'\n"
        "export const tools = [{ name: 'wave', description: 'wave', inputSchema: z.object({}), "
        "handler: async () => ({ bye: true }) }]\n")
    root = root.resolve()

    c = _start(tmp_path, root)
    try:
        assert "datacore_leaving_wave" in {t["name"] for t in c.request("tools/list")["result"]["tools"]}
    finally:
        c.close()
    shutil.rmtree(mod)
    c = _start(tmp_path, root)
    try:
        names = {t["name"] for t in c.request("tools/list")["result"]["tools"]}
    finally:
        c.close()
    assert not [n for n in names if "leaving" in n], "a removed module's tool is still offered"


_MOD = re.compile(r"\.datacore/modules/([A-Za-z0-9_-]+)/")


def test_every_schedule_that_runs_module_code_leaves_with_its_module():
    jobs = (yaml.safe_load((LIB / "jobs" / "manifest.yaml").read_text(encoding="utf-8")) or {}).get("jobs") or []
    installed = {p.parent.name: yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                 for p in (ROOT / ".datacore" / "modules").glob("*/module.yaml")}

    def owned(module, job_name):
        m = installed.get(module) or {}
        declared = (m.get("schedules") or (m.get("provides") or {}).get("schedules")
                    or (m.get("provides") or {}).get("jobs") or [])
        return any((d.get("name") if isinstance(d, dict) else d) == job_name for d in declared)

    orphaned_on_removal = []
    for j in jobs:
        if not isinstance(j, dict):
            continue
        m = _MOD.search(str(j.get("cmd", "")))
        if m and not owned(m.group(1), j.get("name")):
            orphaned_on_removal.append(f"{j.get('name')} -> modules/{m.group(1)}")
    assert not orphaned_on_removal, (
        f"{len(orphaned_on_removal)} scheduled job(s) run module code but are declared outside the "
        "module, so removing or disabling the module leaves them behind: " + ", ".join(orphaned_on_removal))
