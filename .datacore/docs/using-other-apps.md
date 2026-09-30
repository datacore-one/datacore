# Using Datacore from Codex, Cursor and OpenCode

## Find the two programs

1. In a terminal, run:

```bash
which plur-mcp
which datacore-mcp
```

2. Read each line.
   - The line prints an absolute path. Keep it. The steps below paste that path into the app.
   - The line prints nothing. Stop. Install that program, then run `which` again.

## Codex

Codex reads `~/.codex/config.toml`. The two servers sit in `mcp_servers`.

1. Open `~/.codex/config.toml`.
2. If `[mcp_servers.plur]` and `[mcp_servers.datacore]` are already in the file, do not add a second copy. Set each `command` to the path from `which`, then go to step 4.
3. If either table is missing, add the missing one. Leave every other table in the file as it is.

```toml
[mcp_servers.plur]
command = "/absolute/path/to/plur-mcp"

[mcp_servers.datacore]
command = "/absolute/path/to/datacore-mcp"
```

4. Replace each `command` with the path `which` printed for that program.
5. Quit Codex and open it again.
6. Run [Check the connection](#check-the-connection) in Codex.
   - Both results match. Codex is connected.
   - A tool is missing, or the recall returns no engram after the learn step in that section. Stop. Correct this file and start again at step 5.

## Cursor

Cursor reads `.cursor/mcp.json` in the Data folder.

1. Open `.cursor/mcp.json` in the Data folder. Create the file if it is not there.
2. In the Data folder, run `pwd`. Copy that absolute path. A relative path is refused.
3. If `mcpServers` already exists, add `plur` and `datacore` inside it. Leave every other server in place. If the file is new, use the whole block below.
4. Replace the two `command` values with the paths from `which`. Set `DATACORE_PATH` to the path from `pwd`.

```json
{
  "mcpServers": {
    "datacore": {
      "command": "/absolute/path/to/datacore-mcp",
      "env": {
        "DATACORE_PATH": "/absolute/path/to/Data",
        "DATACORE_TOOL_PROFILE": "cursor"
      }
    },
    "plur": {
      "command": "/absolute/path/to/plur-mcp",
      "env": {
        "PLUR_TOOL_PROFILE": "cursor"
      }
    }
  }
}
```

5. Quit Cursor and open it again on the Data folder.
6. Run [Check the connection](#check-the-connection) in Cursor. Use the same stop rule as Codex.

## OpenCode

OpenCode reads `~/.config/opencode/opencode.jsonc`. It needs the `@plur-ai/opencode` plugin and both MCP servers.

1. Open `~/.config/opencode/opencode.jsonc`.
2. Add `@plur-ai/opencode` to the `plugin` list. If the list already exists, append this entry. Do not remove other plugins.
3. Add `plur` and `datacore` under `mcp`. If `mcp` already exists, add these two names and leave every other entry in place. `command` is a list. Its first item is the path `which` printed.

```jsonc
{
  "plugin": ["@plur-ai/opencode"],
  "mcp": {
    "plur": {
      "type": "local",
      "command": ["/absolute/path/to/plur-mcp"],
      "enabled": true
    },
    "datacore": {
      "type": "local",
      "command": ["/absolute/path/to/datacore-mcp"],
      "enabled": true
    }
  }
}
```

4. Quit OpenCode and open it again.
5. Run [Check the connection](#check-the-connection) in OpenCode. Use the same stop rule as Codex.

## Check the connection

Paste this into the app:

```text
Call plur_recall with query "memory". Then call datacore_date with op "today".
```

Right result:

- The recall returns at least one engram, and that engram has an id.
- The date call returns today's date and a three-letter day name. The `result` field looks like `2026-09-30 Wed`.

If the recall list is empty, pick a check word nobody has used before: `check-` followed by the time now, for example `check-1432`. Stay in this app and paste, with your word in place of `check-1432`:

```text
Call plur_learn with statement "Connection check word: check-1432." and type "behavioral". Reply with the engram id only.
```

Copy the id. If the reply has no id, stop. Then run:

```text
Call plur_recall with query "check-1432". Reply with the engram id.
```

The id from the learn step must appear. If it does not, stop. Then remove the test memory:

```text
Call plur_forget with id "<the id>" and scope "primary". Reply "forgotten" when it is done.
```

If the date call fails with `No Python >=3.10 with PyYAML found`, run this in the Data folder, restart the app, and repeat the check:

```bash
datacore update
```

If that Python message appears again, stop.

## Use a memory recorded in another app

Do this only after two of these apps have passed the connection check. If only one app has passed, stop.

1. Pick a new word that you did not use in the connection check: `cross-` followed by the time now, for example `cross-1447`. A word the second app already saw proves nothing.
2. In the first app, paste, with your word in place of `cross-1447`:

```text
Call plur_learn with statement "Cross-app check word: cross-1447." and type "behavioral". Reply with the engram id only.
```

3. Copy the id. If the reply has no id, stop.
4. Open the second app and paste:

```text
Call plur_recall with query "cross-1447". Reply with the engram id.
```

Right result: the id from step 3.

If the second app returns no matching engram, stop. Do not record the word again in the second app.

5. In the second app, remove the test memory:

```text
Call plur_forget with id "<the id>" and scope "primary". Reply "forgotten" when it is done.
```

6. In the first app, run the recall from step 4 again. Right result: the id no longer appears, so forgetting reached both apps.
