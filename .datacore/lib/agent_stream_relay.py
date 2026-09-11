#!/usr/bin/env python3
"""Agent Stream Relay — tiny HTTP service that accepts events from agents
and persists them to the agent-stream JSONL on the always-on host.

Designed to run on nightshift. Agents POST event rows; the JSONL gets
shipped to the Mac via rsync (or any other sync — Tailscale's built-in,
Syncthing, scp cron). The Mac's existing `JsonlAppendWatcher` picks up
the synced file and republishes locally on the /events WebSocket the
Today panel + Pulse view already listen to.

Architecture
============

    Agent (Tris/Data/Miles, anywhere)
         │ POST /events
         ▼
    nightshift:agent_stream_relay.py
         │ append
         ▼
    nightshift:~/.datacore/cos/agent-stream/events-DATE.jsonl   (canonical store)
         │ rsync (every 30s, launchd / cron)
         ▼
    mac:~/.datacore/cos/agent-stream/events-DATE.jsonl
         │ JsonlAppendWatcher (existing in datacored)
         ▼
    /events ws → Today panel · Pulse view

Endpoints
=========

POST /events
    Body: one event JSON object (or a JSON array of objects).
    Header: Authorization: Bearer <RELAY_TOKEN>
    Required fields: type, agent, summary.
    Auto-filled if missing: id (uuid hex), ts (now utc).
    Severity: info | success | warning | error (default info).
    Response: 200 {"ok": true, "written": N}

GET /health
    No auth. {"ok": true, "version": "1.0", "events_today": N}.

That's it. The Mac side never reads from the relay; it reads from its
own filesystem after rsync. Keeping the relay write-only makes auth,
caching, and rate-limiting trivial.

Storage
-------

    ~/.datacore/cos/agent-stream/events-YYYY-MM-DD.jsonl

One JSON object per line, identical shape to what the datacore-app
daemon's AgentStream expects.

Deploy on nightshift
====================

    # 1) Copy to the server (or git-pull if .datacore/ is synced):
    scp lib/agent_stream_relay.py nightshift:~/.datacore/lib/

    # 2) systemd unit at /etc/systemd/system/datacore-agent-relay.service:
        [Unit]
        Description=Datacore Agent Stream Relay
        After=network.target

        [Service]
        Type=simple
        User=deploy
        WorkingDirectory=<HOME>
        Environment=PORT=18891
        ExecStart=/usr/bin/python3 <HOME>/Data/.datacore/lib/agent_stream_relay.py
        Restart=always
        RestartSec=5

        [Install]
        WantedBy=multi-user.target

    # 3) systemctl enable --now datacore-agent-relay
    # 4) Read the generated token: cat <HOME>/.datacore/cos/agent-stream/relay.token

Set up the rsync on the Mac (launchd plist) — one-liner pulling
nightshift:~/.datacore/cos/agent-stream/ → ~/.datacore/cos/agent-stream/
every 30s. The watcher picks up changes immediately.

Resource use: ~12 MB RSS idle, single Python process, no deps beyond stdlib.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from agent_stream_store import EventConflict, append_events
from file_utils import atomic_write_text, file_lock
from http_utils import BoundedHTTPServer, RequestError, bearer_matches, read_body

EVENT_LOG_DIR = Path.home() / ".datacore" / "cos" / "agent-stream"
TOKEN_FILE = Path(
    os.environ.get(
        "RELAY_TOKEN_FILE",
        str(EVENT_LOG_DIR / "relay.token"),
    )
)
DEFAULT_PORT = int(os.environ.get("PORT", "18891"))
DEFAULT_HOST = os.environ.get("HOST", "127.0.0.1")

VERSION = "1.0"


# ── Storage helpers ──────────────────────────────────────────────────────────


def _today_log() -> Path:
    EVENT_LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    return EVENT_LOG_DIR / f"events-{datetime.now(tz=timezone.utc).date().isoformat()}.jsonl"


def _ensure_token() -> str:
    """Read or generate the relay's bearer token. 0600 perms."""
    TOKEN_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with file_lock(TOKEN_FILE):
        try:
            token = TOKEN_FILE.read_text().strip()
        except FileNotFoundError:
            token = secrets.token_urlsafe(32)
            atomic_write_text(TOKEN_FILE, token)
        if not token:
            raise ValueError("relay token file is empty; configure a nonempty token")
        os.chmod(TOKEN_FILE, 0o600)
        return token


def _normalise(ev: dict[str, Any]) -> dict[str, Any]:
    """Fill in id/ts and clamp fields to the agent-stream contract."""
    for key in ("type", "agent", "summary"):
        if not isinstance(ev.get(key), str) or not ev[key]:
            raise ValueError(f"{key} must be a nonempty string")
    for key in ("id", "ts"):
        if key in ev and (not isinstance(ev[key], str) or not ev[key] or len(ev[key]) > 256):
            raise ValueError(f"{key} must be a nonempty bounded string")
    if "severity" in ev and not isinstance(ev["severity"], str):
        raise ValueError("severity must be a string")
    if ev.get("details") is not None and not isinstance(ev["details"], dict):
        raise ValueError("details must be an object")
    out: dict[str, Any] = {
        "id": ev.get("id") or uuid.uuid4().hex,
        "ts": ev.get("ts") or datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "type": str(ev.get("type", "agent.event"))[:64],
        "agent": str(ev.get("agent", "agent"))[:64],
        "summary": str(ev.get("summary", ""))[:300],
        "severity": ev.get("severity", "info"),
    }
    if out["severity"] not in {"info", "success", "warning", "error"}:
        out["severity"] = "info"
    details = ev.get("details")
    out["details"] = details if isinstance(details, dict) else None
    return out


# ── HTTP handler ─────────────────────────────────────────────────────────────


class RelayHandler(BaseHTTPRequestHandler):
    server_version = f"DatacoreAgentStreamRelay/{VERSION}"
    token: str = ""  # set by serve()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        # Default access log is noisy; only log warnings via log_error.
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        if not self.token:
            self._send_json(503, {"ok": False, "error": "authentication is not configured"})
            return False
        if not bearer_matches(self.headers, self.token):
            self._send_json(401, {"ok": False, "error": "invalid bearer token"})
            return False
        return True

    def do_GET(self) -> None:
        if urlparse(self.path).path.rstrip("/") != "/health":
            return self._send_json(404, {"ok": False, "error": "not found"})
        # Health is public and constant-cost; it must not scan private logs.
        self._send_json(200, {"ok": True, "version": VERSION})

    def do_POST(self) -> None:
        if urlparse(self.path).path.rstrip("/") != "/events":
            return self._send_json(404, {"ok": False, "error": "not found"})
        if not self._check_auth():
            return

        try:
            raw = read_body(self)
            data = json.loads(raw)
        except RequestError as exc:
            return self._send_json(exc.status, {"ok": False, "error": str(exc)})
        except (ValueError, UnicodeError, RecursionError):
            return self._send_json(400, {"ok": False, "error": "invalid JSON"})

        rows: list[dict[str, Any]] = data if isinstance(data, list) else [data]
        if not rows or len(rows) > 1000 or any(not isinstance(ev, dict) for ev in rows):
            return self._send_json(400, {"ok": False, "error": "expected 1 to 1000 event objects"})
        try:
            rows = [_normalise(ev) for ev in rows]
            # Validate the entire serialization before opening the store.
            json.dumps(rows, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError):
            return self._send_json(400, {"ok": False, "error": "invalid event fields"})
        try:
            written = append_events(_today_log(), rows)
        except EventConflict:
            return self._send_json(409, {"ok": False, "error": "event ID conflict"})
        except (OSError, ValueError):
            return self._send_json(500, {"ok": False, "error": "event storage unavailable"})

        self._send_json(200, {"ok": True, "written": written})


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    token = _ensure_token()
    RelayHandler.token = token
    server = BoundedHTTPServer((host, port), RelayHandler)
    print(f"[relay] listening on http://{host}:{port}  ·  token at {TOKEN_FILE}", flush=True)
    print(f"[relay] storage: {EVENT_LOG_DIR}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[relay] shutting down", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    serve()
