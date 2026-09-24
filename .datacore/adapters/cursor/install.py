#!/usr/bin/env python3
"""Wire Datacore into Cursor for one installation.

    python3 .datacore/adapters/cursor/install.py            # install into <root>/.cursor/
    python3 .datacore/adapters/cursor/install.py --dry-run  # show what would be written
    python3 .datacore/adapters/cursor/install.py doctor     # live checks, ok / FAIL / n-a

What it writes, all under <root>/.cursor/ (gitignored: every value is a local path):

- mcp.json: `datacore` and `plur` servers with explicit paths, DATACORE_PATH,
  DATACORE_PYTHON (the install's venv) and the `cursor` tool profiles. Cursor
  caps a workspace at ~40 MCP tools across all servers; the profiles bring
  Datacore to 13 (datacore_call reaches the rest) and PLUR to 11.
- hooks.json: hook.py (Datacore's guards under Cursor's payload shapes) on
  preToolUse and beforeShellExecution, plus PLUR's own Cursor hooks when its
  shim exists. The PLUR logic stays in PLUR; only the registration is here.

Context needs nothing Cursor-specific: Cursor reads AGENTS.md, which
context_merge writes from the CLAUDE layers (install checks that it exists).

Merges by ownership: only the `datacore` / `plur` servers and the hook
entries this installer writes (fingerprinted by command) are replaced. A
config that does not parse is refused, never overwritten.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

HERE = Path(__file__).resolve().parent
BRIDGE_MARK = "adapters/cursor/hook.py"
PLUR_SUBCOMMANDS = ("hook-cursor-session-start", "hook-cursor-guard", "hook-cursor-post-tool", "hook-cursor-stop")
CURSOR_TOOL_CAP = 40


class InstallError(Exception):
    pass


@dataclass
class Tools:
    datacore_mcp: str | None
    plur_mcp: str | None
    plur_hook: str | None
    python: str


def default_root() -> Path:
    return Path(os.environ.get("DATACORE_PATH") or HERE.parents[2]).resolve()   # <root>/.datacore/adapters/cursor


def resolve_tools(root: Path) -> Tools:
    venv_py = root / ".datacore" / "venv" / "bin" / "python"
    plur_hook = Path.home() / ".plur" / "bin" / "plur-hook"
    plur_shim = Path.home() / ".plur" / "bin" / "plur-mcp"
    return Tools(
        datacore_mcp=shutil.which("datacore-mcp"),
        plur_mcp=shutil.which("plur-mcp") or (str(plur_shim) if plur_shim.exists() else None),
        plur_hook=str(plur_hook) if plur_hook.exists() else None,
        python=str(venv_py) if venv_py.exists() else sys.executable,
    )


def merge_mcp(existing: dict, root: Path, tools: Tools) -> dict:
    cfg = dict(existing)
    servers = dict(cfg.get("mcpServers") or {})
    if not tools.datacore_mcp:
        raise InstallError("datacore-mcp not found on PATH; install it: npm install -g @datacore-one/mcp")
    env = {"DATACORE_PATH": str(root)}
    if "venv" in tools.python:
        env["DATACORE_PYTHON"] = tools.python
    env["DATACORE_TOOL_PROFILE"] = "cursor"
    servers["datacore"] = {"command": tools.datacore_mcp, "env": env}
    if tools.plur_mcp:
        servers["plur"] = {"command": tools.plur_mcp, "env": {"PLUR_TOOL_PROFILE": "cursor"}}
    cfg["mcpServers"] = servers
    return cfg


def _ours(entry: dict) -> bool:
    command = str(entry.get("command", ""))
    if BRIDGE_MARK in command:
        return True
    return "plur-hook" in command and any(sub in command for sub in PLUR_SUBCOMMANDS)


def merge_hooks(existing: dict, root: Path, tools: Tools) -> dict:
    cfg = dict(existing)
    hooks = {event: [h for h in (entries or []) if isinstance(h, dict) and not _ours(h)]
             for event, entries in (cfg.get("hooks") or {}).items()}
    bridge = f"{tools.python} {root / '.datacore' / 'adapters' / 'cursor' / 'hook.py'}"
    additions: dict[str, list[dict]] = {
        "preToolUse": [{"command": bridge, "timeout": 25, "failClosed": False}],
        "beforeShellExecution": [{"command": bridge, "timeout": 25, "failClosed": False}],
    }
    if tools.plur_hook:
        additions["sessionStart"] = [{"command": f"{tools.plur_hook} hook-cursor-session-start", "timeout": 10, "failClosed": False}]
        additions["preToolUse"].append({"command": f"{tools.plur_hook} hook-cursor-guard", "timeout": 3, "failClosed": False})
        additions["postToolUse"] = [{"command": f"{tools.plur_hook} hook-cursor-post-tool", "timeout": 10, "failClosed": False}]
        additions["stop"] = [{"command": f"{tools.plur_hook} hook-cursor-stop", "timeout": 3, "failClosed": False}]
    for event, entries in additions.items():
        hooks[event] = hooks.get(event, []) + entries
    cfg["version"] = cfg.get("version", 1)
    cfg["hooks"] = {k: v for k, v in hooks.items() if v}
    return cfg


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text() or "{}")
    except ValueError as exc:
        raise InstallError(f"{path} is not valid JSON ({exc}); fix or move it, nothing was changed") from exc
    if not isinstance(data, dict):
        raise InstallError(f"{path} is not a JSON object; nothing was changed")
    return data


def install(root: Path, tools: Tools, dry_run: bool = False) -> dict[Path, dict]:
    cursor = root / ".cursor"
    mcp_path, hooks_path = cursor / "mcp.json", cursor / "hooks.json"
    planned = {mcp_path: merge_mcp(_read(mcp_path), root, tools),
               hooks_path: merge_hooks(_read(hooks_path), root, tools)}
    if not dry_run:
        cursor.mkdir(exist_ok=True)
        for path, data in planned.items():
            path.write_text(json.dumps(data, indent=2) + "\n")
    return planned


# --- Cursor's per-project MCP approval ---------------------------------------

def cursor_project_dir(root: Path, home: Path | None = None) -> Path:
    """Where Cursor keeps per-project state: ~/.cursor/projects/<path with / as ->."""
    return (home or Path.home()) / ".cursor" / "projects" / str(root).strip("/").replace("/", "-")


def approved_servers(root: Path, home: Path | None = None) -> set[str] | None:
    """Project MCP servers Cursor has been told to load, or None if it has no record.

    Cursor skips a project's .cursor/mcp.json servers until the user approves
    them once (entries are `<name>-<hash>`). That is a consent step, so this
    only reads it; approving is the user's to do in Cursor.
    """
    path = cursor_project_dir(root, home) / "mcp-approvals.json"
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    keys = data if isinstance(data, list) else list(data) if isinstance(data, dict) else []
    return {str(k).rsplit("-", 1)[0] for k in keys}


# --- doctor ---------------------------------------------------------------

def _tools_list(command: str, env: dict, cwd: Path) -> list[str]:
    """Live MCP handshake; the tool names the server advertises."""
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "cursor-doctor", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    p = subprocess.Popen([command], cwd=cwd, env={**os.environ, **env}, text=True,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        for m in msgs:           # paced: a burst before initialize completes is dropped
            p.stdin.write(json.dumps(m) + "\n"); p.stdin.flush(); time.sleep(2)
        deadline = time.time() + 30
        while time.time() < deadline:
            line = p.stdout.readline()
            if not line:
                break
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if msg.get("id") == 2:
                return [t["name"] for t in msg["result"]["tools"]]
        return []
    finally:
        p.kill()


def doctor(root: Path) -> int:
    results: list[tuple[str, str, str]] = []
    try:
        mcp = _read(root / ".cursor" / "mcp.json")
        hooks = _read(root / ".cursor" / "hooks.json")
    except InstallError as exc:
        print(f"FAIL config  {exc}")
        return 1
    total = 0
    for name in ("datacore", "plur"):
        srv = (mcp.get("mcpServers") or {}).get(name)
        if not srv:
            results.append(("FAIL" if name == "datacore" else "n-a", f"mcp:{name}", "not registered"))
            continue
        names = _tools_list(srv["command"], srv.get("env", {}), root)
        total += len(names)
        state = "ok" if names else "FAIL"
        results.append((state, f"mcp:{name}", f"{len(names)} tools" if names else "no tools/list response"))
    results.append(("ok" if 0 < total <= CURSOR_TOOL_CAP else "FAIL", "tool budget",
                    f"{total} of Cursor's ~{CURSOR_TOOL_CAP} (other servers in ~/.cursor/mcp.json also count)"))
    registered = [h for hs in (hooks.get("hooks") or {}).values() for h in hs if BRIDGE_MARK in str(h.get("command", ""))]
    results.append(("ok" if len(registered) >= 2 else "FAIL", "guard hooks", f"{len(registered)} bridge entries"))
    if registered:
        probe = subprocess.run(registered[0]["command"].split(" ", 1), input=json.dumps(
            {"hook_event_name": "preToolUse", "tool_name": "Shell", "tool_input": {"command": "true"}}),
            capture_output=True, text=True, timeout=30)
        results.append(("ok" if probe.returncode == 0 and not probe.stdout.strip() else "FAIL",
                        "guard bridge", "benign call passes silently" if not probe.stdout.strip() else probe.stdout.strip()[:120]))
    approved = approved_servers(root)
    for name in ("datacore", "plur"):
        if name not in (mcp.get("mcpServers") or {}):
            continue
        if approved is not None and name in approved:
            results.append(("ok", f"approved:{name}", "Cursor will load it"))
        else:
            results.append(("FAIL", f"approved:{name}",
                            "Cursor skips it until approved once: in the Cursor app open this folder, "
                            "Settings > MCP > enable it; in the terminal run `cursor-agent` here and approve"))
    agents = root / "AGENTS.md"
    results.append(("ok" if agents.exists() else "FAIL", "AGENTS.md",
                    "present" if agents.exists() else "missing: run context_merge.py rebuild --path <root> --emit"))
    for state, check, detail in results:
        print(f"{state:<5} {check:<13} {detail}")
    return 0 if all(s != "FAIL" for s, _, _ in results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", nargs="?", default="install", choices=["install", "doctor"])
    ap.add_argument("--root", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    root = (args.root or default_root()).resolve()
    if args.command == "doctor":
        return doctor(root)
    try:
        planned = install(root, resolve_tools(root), dry_run=args.dry_run)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for path, data in planned.items():
        print(f"{'would write' if args.dry_run else 'wrote'} {path}")
        if args.dry_run:
            print(json.dumps(data, indent=2))
    if not (root / "AGENTS.md").exists():
        print("note: AGENTS.md missing; run: python3 .datacore/lib/context_merge.py rebuild --path "
              f"{root} --emit", file=sys.stderr)
    print("next: open this folder in Cursor, then run: python3 .datacore/adapters/cursor/install.py doctor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
