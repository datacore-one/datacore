# Agent Stream — cross-host activity capture

How Tris, Data, Miles, and any future agent get their activity into
the Today panel + Pulse view, regardless of which machine they run on.

## Architecture (one picture)

```
                   AGENTS RUNNING ANYWHERE
                          │
                          │  agent_emit.emit(...)
                          │
              ┌───────────┴───────────┐
              │ AGENT_STREAM_RELAY_URL  │ no env var
              │ is set?                 │
              └───────────┬───────────┘
                          │
            yes ───────────────────── no
              │                        │
              │ HTTP POST              │ append directly
              ▼                        ▼
      ┌──────────────────┐    ~/.datacore/cos/agent-stream/
      │  RELAY (nightshift)│   events-DATE.jsonl   (local host)
      │  port 18891        │
      └──────────┬─────────┘
                 │ append
                 ▼
   nightshift:~/.datacore/cos/agent-stream/events-DATE.jsonl
                 │
                 │ rsync (Mac launchd, every 30s)
                 ▼
        mac:~/.datacore/cos/agent-stream/events-DATE.jsonl
                 │
                 │ JsonlAppendWatcher (datacored on Mac)
                 ▼
        /events WebSocket
                 │
                 ▼
        Today panel · Pulse view (live)
```

The canonical store lives on **nightshift** because nightshift is
always-on. The Mac is a viewer that pulls the file. No agent ever
writes to the Mac directly.

## Components

| File | Where it runs | Purpose |
|---|---|---|
| `lib/agent_emit.py` | every agent (Tris, Data, …) | tiny helper, `emit(...)` calls |
| `lib/agent_stream_relay.py` | nightshift | accepts POSTs, writes JSONL |
| `lib/agent_stream_rsync.sh` | Mac | pulls JSONL from nightshift |
| `lib/com.datacore.agent-stream-rsync.plist` | Mac LaunchAgent | runs rsync every 30s |
| `lib/datacore-agent-relay.service` | nightshift systemd | runs the relay |

## Deploy on nightshift

1. Make sure `lib/agent_stream_relay.py` is on nightshift. If
   `~/Data/.datacore/` is git-synced to nightshift, it's already there.
   Otherwise: `scp -r ~/Data/.datacore/lib nightshift:~/Data/.datacore/`.

2. Install the systemd unit:

   ```bash
   ssh nightshift '
     sudo cp ~/Data/.datacore/lib/datacore-agent-relay.service /etc/systemd/system/
     sudo systemctl daemon-reload
     sudo systemctl enable --now datacore-agent-relay
     sudo systemctl status datacore-agent-relay
   '
   ```

3. Read the auto-generated bearer token:

   ```bash
   scp nightshift:.datacore/cos/agent-stream/relay.token \
       ~/Data/.datacore/env/agent-stream-relay.token
   chmod 0600 ~/Data/.datacore/env/agent-stream-relay.token
   ```

4. Verify:

   ```bash
   curl http://nightshift:18891/health
   # → {"ok": true, "version": "1.0", "events_today": 0}
   ```

## Wire agents to the relay

Each agent that runs on a non-Mac host needs two env vars in its
runtime environment (systemd EnvironmentFile, .env, shell rc):

```bash
AGENT_STREAM_RELAY_URL=http://nightshift:18891
AGENT_STREAM_RELAY_TOKEN=<contents-of-relay.token>
```

Then in the agent's code:

```python
import sys
sys.path.insert(0, '<HOME>/Data/.datacore/lib')
from agent_emit import emit, emit_message, emit_task

# Outbound message:
emit_message("data", "telegram", "Replied to @gregor: kept it short",
             message_id=str(sent_msg.id))

# Task lifecycle:
emit_task("tris", "research-fairdrop-competitors", "completed",
          summary="Found 3 with relevant moats")

# Generic:
emit("agent.observation", agent="miles",
     summary="3 PRs reviewed", severity="success",
     details={"reviewed": 3, "blocked": 1})
```

If `AGENT_STREAM_RELAY_URL` isn't set, the helper falls back to local
file append — same JSONL shape, different location. Agents on the same
host as a datacored install Just Work that way.

## Wire the Mac to read from nightshift

Install the launchd plist that runs rsync every 30s:

```bash
cp ~/Data/.datacore/lib/com.datacore.agent-stream-rsync.plist \
   ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.datacore.agent-stream-rsync.plist
```

Verify:

```bash
launchctl list | grep agent-stream
tail -f /tmp/datacore-agent-stream-rsync.log
```

The Mac's datacored already runs `JsonlAppendWatcher` on
`~/.datacore/cos/agent-stream/`. When rsync writes a new JSONL line,
the watcher fires and the WebSocket push lands in the Today panel
within ~2 seconds.

Edit the plist's `EnvironmentVariables` block if your nightshift
hostname or deploy username differs.

## Lens migration path

When Lens is installed on a host, the agent's call site changes from
`emit()` to `lens.capture()`. The event shape is identical and the
Mac still reads the same JSONL files (Lens persists in the same
location). No frontend or watcher changes.

## Auth quick reference

| Layer | Credential | Where it lives |
|---|---|---|
| Relay HTTP | bearer token (32-byte url-safe) | `~/.datacore/cos/agent-stream/relay.token` (nightshift, autogen) |
| Mac viewer | datacored bearer | `~/.datacore/app/datacored.token` (autogen) |
| rsync | SSH key | `~/.ssh/id_*` (your nightshift access) |

Three independent secrets. Rotate the relay token by deleting
`relay.token` on nightshift and restarting the service; redistribute
to the agents via your usual env-var sync.

## Troubleshooting

**No events showing on the Mac.**
1. Hit `/health` on the relay — is it up? `curl http://nightshift:18891/health`.
2. SSH to nightshift and tail today's JSONL — are events landing? `tail -f .datacore/cos/agent-stream/events-$(date +%Y-%m-%d).jsonl`
3. Check the rsync log: `tail -50 /tmp/datacore-agent-stream-rsync.log`. SSH errors? Wrong path?
4. Check the local datacored log for the JsonlAppendWatcher.

**Agent emits but relay returns 401.**
The agent's `AGENT_STREAM_RELAY_TOKEN` doesn't match `relay.token` on
nightshift. The agent_emit helper falls back to local file append on
failure, so you'll see events on the agent's host but not in the
canonical store.

**Mac watcher doesn't fire after rsync.**
The plist uses `--inplace` so mtime updates trigger watchdog. If it's
still not firing, restart datacored to force a replay
(`pkill -9 -f datacored && sleep 1 && open -F /Applications/datacore-app.app`).
