# Cursor adapter

This adapter makes Datacore work in Cursor, in both the editor and the `cursor-agent` CLI, for one installation.

```bash
python3 .datacore/adapters/cursor/install.py            # writes <root>/.cursor/mcp.json and hooks.json
python3 .datacore/adapters/cursor/install.py doctor     # live checks: ok / FAIL / n-a
```

| Layer | How Cursor gets it |
|---|---|
| Tools | `datacore` and `plur` MCP servers in `.cursor/mcp.json`, both on the `cursor` tool profile. Cursor caps a workspace at about 40 MCP tools across all servers. The profile gives 13 Datacore tools; `datacore_call` reaches the other 59 module tools (GTD, CRM, goals, …). PLUR gets 12. |
| Context | `AGENTS.md` at the root, which Cursor reads natively. `context_merge.py rebuild --emit` writes it from the CLAUDE layers. |
| Commands | `/today`, `/wrap-up`, … : ask the agent. It calls `datacore_command_run` and follows the returned steps. The root `AGENTS.md` says so. |
| Guards | `hook.py` on `preToolUse` and `beforeShellExecution`. It translates Cursor's payloads (`Shell`, `Write`, bare `command`) into the Claude shape and runs the real guard scripts: the org date/weekday guard and the restricted-hosts guard. A block becomes Cursor's `{"permission":"deny"}`. |
| Memory | PLUR's own Cursor hooks (`plur-hook hook-cursor-*`), registered here. Their logic lives in PLUR. |

## Why a bridge rather than Cursor's Claude-hook import

Cursor imports Claude Code hooks from `.claude/settings.json`, and does so by default. It hands them Cursor's tool names, though. `restricted_hosts_guard.py` only acts on `tool_name == "Bash"`, so under Cursor's `"Shell"` it would let everything through without a word. The bridge presents the payload in Claude's shape, so the guards behave as they do in Claude Code.

## Behaviour

- The bridge never emits an allow; saying nothing leaves Cursor's own approval flow in charge.
- It fails open, except for shell commands. A shell event it cannot evaluate is denied, because the host guard is contractual.
- The installer replaces only the `datacore`/`plur` servers and the hook entries it wrote, and keeps everything else.
- A config that doesn't parse is refused, never overwritten. Re-running the installer changes nothing.
- `.cursor/` is gitignored, because every value in it is a machine-local path.
