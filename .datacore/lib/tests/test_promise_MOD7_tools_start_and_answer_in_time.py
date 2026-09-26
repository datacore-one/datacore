"""MOD-7: The Datacore tools my AI uses start reliably and answer within their time limit,
or give an honest error.

Kind: production contract (this machine). The MCP servers are started exactly as
.mcp.json configures them for the AI (command, args, env) against the real
installation, and asked read-only questions. Time limit: 30 s, the MCP timeout the
harness applies (audit C9: a 30 s timeout turned a slow answer into a false
"broken"). An honest error is a JSON-RPC answer with isError, in time.

Seeded failure: the server does not come up within 30 s (the datacore server failed
to connect in the promise-drafting session), or a read-only tool the AI calls every
session takes longer than 30 s to answer -- measured 2026-09-26: datacore_status
32 s, datacore_search 41 s, datacore_date 5 s, startup 10-12 s.
"""
import json
import os
import time
from pathlib import Path

import pytest

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
import sys  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_client import McpClient, McpTimeout  # noqa: E402

LIMIT_S = 30.0
pytestmark = pytest.mark.production


def _start(name):
    cfg = json.loads((ROOT / ".mcp.json").read_text())["mcpServers"][name]
    env = dict(os.environ, **cfg.get("env", {}))
    t = time.monotonic()
    c = McpClient([cfg["command"], *cfg.get("args", [])], env=env, cwd=str(ROOT))
    try:
        c.initialize(timeout=LIMIT_S)
        c.request("tools/list", timeout=LIMIT_S)
    except McpTimeout:
        c.close()
        pytest.fail(f"the {name} MCP server did not start and list its tools within {LIMIT_S:.0f} s")
    return c, time.monotonic() - t


def _timed_call(c, tool, args):
    t = time.monotonic()
    try:
        r = c.call(tool, args, timeout=LIMIT_S)
    except McpTimeout:
        return None, time.monotonic() - t
    return r, time.monotonic() - t


@pytest.mark.parametrize("name", ["datacore", "datacore-app"])
def test_the_server_starts_within_the_limit(name):
    c, took = _start(name)
    c.close()
    assert took <= LIMIT_S, f"{name} took {took:.1f} s to start"


def test_an_unknown_call_gets_an_honest_error_in_time():
    c, _ = _start("datacore")
    try:
        r, took = _timed_call(c, "datacore_no_such_tool", {})
    finally:
        c.close()
    assert r is not None, f"an unknown tool call got no answer within {LIMIT_S:.0f} s"
    assert r.get("error") or (r.get("result") or {}).get("isError"), r


@pytest.mark.parametrize("tool,args", [
    ("datacore_status", {}),
    ("datacore_search", {"query": "journal", "limit": 1}),
])
def test_a_read_only_tool_answers_within_the_limit(tool, args):
    c, _ = _start("datacore")
    try:
        r, took = _timed_call(c, tool, args)
    finally:
        c.close()
    assert r is not None, f"{tool} gave no answer and no error within {LIMIT_S:.0f} s"
    assert "result" in r or "error" in r, r
