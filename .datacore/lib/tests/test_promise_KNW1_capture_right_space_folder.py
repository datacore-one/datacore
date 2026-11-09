"""KNW-1: When I ask to keep a note, it lands as a journal entry or knowledge note in the
right space, in the right folder for its kind.

Kind: deterministic. The real datacore MCP server (the `datacore_capture` tool the AI
calls; 2-datacore/2-projects/datacore-mcp/dist/index.js, as configured in .mcp.json)
runs over stdio against a tmp installation with a personal and a team space.

Seeded failure: a journal entry lands outside today's journal page; or a knowledge
note cannot be addressed to a space or a kind, so every note lands flat in the
personal space's 3-knowledge/ root instead of e.g. team/3-knowledge/zettel/.
"""
import json
import os
import re
import shutil
import sys

from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_client import McpClient  # noqa: E402

SERVER = ROOT / "2-datacore" / "2-projects" / "datacore-mcp" / "dist" / "index.js"
KINDS = ("zettel", "literature", "reference", "pages")


def _install(tmp_path):
    root = (tmp_path / "Data")
    for rel in (".datacore", "0-personal/.datacore", "0-personal/notes/journals", "0-personal/3-knowledge",
                "1-team/.datacore", "1-team/journal", "1-team/3-knowledge"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    for kb in ("0-personal/3-knowledge", "1-team/3-knowledge"):
        for k in KINDS:
            (root / kb / k).mkdir()
    (root / "0-personal/.datacore/config.yaml").write_text("space:\n  name: personal\n  type: personal\n  owner: owner\n")
    (root / "1-team/.datacore/config.yaml").write_text("space:\n  name: team\n  type: team\n  owner: owner\n")
    return root.resolve()


@pytest.fixture
def server(tmp_path):
    node = shutil.which("node") or str(Path.home() / ".nvm/versions/node/v24.10.0/bin/node")
    assert Path(node).exists() and SERVER.is_file(), f"server not installed: {SERVER}"
    root = _install(tmp_path)
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "DATACORE_PATH": str(root),
           "DATACORE_LIB": str(LIB), "DATACORE_PYTHON": sys.executable, "DATACORE_TIMEZONE": "UTC"}
    c = McpClient([node, str(SERVER)], env=env, cwd=str(tmp_path))
    c.initialize(timeout=30)
    yield c, root
    c.close()


def _result(resp):
    assert "result" in resp, resp
    text = "".join(p.get("text", "") for p in resp["result"].get("content", []))
    try:
        return json.loads(text)
    except ValueError:
        return {"raw": text, "isError": resp["result"].get("isError")}


def test_a_journal_entry_lands_on_todays_page_of_the_personal_space(server):
    c, root = server
    out = _result(c.call("datacore_capture", {"type": "journal", "content": "Decided to ship KNW-1."}))
    assert out.get("success"), out
    page = Path(out["path"])
    assert page.parent == root / "0-personal" / "notes" / "journals", page
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", page.name), page
    assert "Decided to ship KNW-1." in page.read_text()


def test_a_knowledge_note_can_be_addressed_to_a_space_and_lands_in_its_kind_folder(server):
    c, root = server
    tools = {t["name"]: t for t in c.request("tools/list")["result"]["tools"]}
    props = tools["datacore_capture"]["inputSchema"].get("properties", {})
    space_arg = next((p for p in props if "space" in p.lower()), None)
    kind_arg = next((p for p in props if p.lower() in ("kind", "folder", "note_type", "note_kind", "category")), None)
    assert space_arg and kind_arg, (
        "datacore_capture offers no way to choose the space and the kind of a knowledge note "
        f"(its arguments are {sorted(props)}), so every note lands in one place")
    out = _result(c.call("datacore_capture", {"type": "knowledge", "title": "Team concept",
                                              "content": "An atomic idea for the team.",
                                              space_arg: "team", kind_arg: "zettel"}))
    assert out.get("success"), out
    note = Path(out["path"])
    assert note.parent == root / "1-team" / "3-knowledge" / "zettel", (
        f"a team zettel landed in {note.parent}, not in 1-team/3-knowledge/zettel")
