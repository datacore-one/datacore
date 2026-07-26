#!/bin/bash
# Shared exa MCP server, streamable-HTTP transport.
#
# One instance serves every Claude Code session instead of each session
# spawning its own stdio child. The upstream package ships this build as
# `smithery/shttp/index.cjs` and self-reports as a stateless server, so
# concurrent clients are safe.
#
# Managed by ~/Library/LaunchAgents/com.datacore.exa-mcp.plist
# Consumed via .mcp.json:  "exa": { "type": "http", "url": "http://127.0.0.1:8823/mcp" }
#
# Install the server with:  npm install -g exa-mcp-server

set -euo pipefail

PORT="${EXA_MCP_PORT:-8823}"
DATACORE_ROOT="${DATACORE_ROOT:-$HOME/Data}"
ENV_FILE="$DATACORE_ROOT/.datacore/env/.env"

# Resolve node from PATH — launchd supplies PATH via the plist's
# EnvironmentVariables. Avoids pinning an nvm version that a later
# upgrade would silently invalidate.
NODE_BIN="$(command -v node || true)"
if [ -z "$NODE_BIN" ]; then
  echo "FATAL: node not found on PATH — check EnvironmentVariables in the plist" >&2
  exit 1
fi

# Resolve the globally-installed package rather than hardcoding its path.
GLOBAL_ROOT="$(npm root -g 2>/dev/null || true)"
SERVER="$GLOBAL_ROOT/exa-mcp-server/smithery/shttp/index.cjs"

if [ ! -f "$SERVER" ]; then
  echo "FATAL: exa shttp build missing at $SERVER — run: npm install -g exa-mcp-server" >&2
  exit 1
fi

# EXA_API_KEY lives in the env file, never in this script or the plist.
if [ -f "$ENV_FILE" ]; then
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
fi

export PORT
exec "$NODE_BIN" "$SERVER"
