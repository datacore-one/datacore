#!/usr/bin/env python3
"""Call a plur MCP tool over stdio when the in-session MCP connection is dead.

Spawns the plur MCP server, performs the initialize handshake, issues one
tools/call, waits for its response, prints the result content to stdout.

Usage:
    python3 plur_mcp_call.py <tool_name> '<json_arguments>'
    python3 plur_mcp_call.py plur_feedback '{"signals":[{"id":"ENG-...","signal":"positive"}]}'

Exit codes: 0 ok, 1 tool error, 2 transport/timeout failure.
"""
import json
import os
import subprocess
import sys

SERVER = os.path.expanduser(
    "~/Data/5-plur/2-projects/plur/packages/mcp/dist/index.js"
)
TIMEOUT = 240


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    tool = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    env = dict(os.environ, PLUR_TOOL_PROFILE="full")
    proc = subprocess.Popen(
        ["node", SERVER],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=(None if os.environ.get("PLUR_MCP_CALL_DEBUG") else subprocess.DEVNULL),
        env=env,
        text=True,
    )

    def send(msg: dict) -> None:
        proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()

    send({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "plur-mcp-call", "version": "1.0"},
        },
    })
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    })

    import threading

    result = {}

    def reader() -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("id") == 2:
                result.update(msg)
                return

    t = threading.Thread(target=reader, daemon=True)
    t.start()
    t.join(TIMEOUT)
    proc.stdin.close()
    proc.terminate()

    if not result:
        print(f"no response for {tool} within {TIMEOUT}s", file=sys.stderr)
        return 2
    if "error" in result:
        print(json.dumps(result["error"]))
        return 1
    for block in result.get("result", {}).get("content", []):
        if block.get("type") == "text":
            print(block["text"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
