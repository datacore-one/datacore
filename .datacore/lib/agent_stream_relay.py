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
=======

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

EVENT_LOG_DIR = Path.home() / ".datacore" / "cos" / "agent-stream"
TOKEN_FILE = Path(
    os.environ.get(
        "RELAY_TOKEN_FILE",
        str(EVENT_LOG_DIR / "relay.token"),
    )
)
DEFAULT_PORT = int(os.environ.get("PORT", "18891"))
DEFAULT_HOST = os.environ.get("HOST", "0.0.0.0")  # bind all so Tailscale reaches us

VERSION = "1.0"


# ── Storage helpers ──────────────────────────────────────────────────────────


def _today_log() -> Path:
    EVENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return EVENT_LOG_DIR / f"events-{datetime.now(tz=timezone.utc).date().isoformat()}.jsonl"


def _ensure_token() -> str:
    """Read or generate the relay's bearer token. 0600 perms."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.write_text(token)
    try:
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    print(f"[relay] generated new token at {TOKEN_FILE}", flush=True)
    return token


def _normalise(ev: dict[str, Any]) -> dict[str, Any]:
    """Fill in id/ts and clamp fields to the agent-stream contract."""
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self) -> bool:
        if not self.token:
            return True
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            self._send_json(401, {"ok": False, "error": "missing bearer token"})
            return False
        if header[len("Bearer "):].strip() != self.token:
            self._send_json(401, {"ok": False, "error": "invalid bearer token"})
            return False
        return True

    def do_GET(self) -> None:
        if urlparse(self.path).path.rstrip("/") != "/health":
            return self._send_json(404, {"ok": False, "error": "not found"})
        log = _today_log()
        count = 0
        try:
            if log.is_file():
                count = sum(1 for _ in log.open())
        except OSError:
            pass
        self._send_json(200, {"ok": True, "version": VERSION, "events_today": count})

    def do_POST(self) -> None:
        if urlparse(self.path).path.rstrip("/") != "/events":
            return self._send_json(404, {"ok": False, "error": "not found"})
        if not self._check_auth():
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > 1_000_000:
            return self._send_json(400, {"ok": False, "error": "missing or oversized body"})
        try:
            raw = self.rfile.read(length)
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError):
            return self._send_json(400, {"ok": False, "error": "invalid JSON"})

        rows: list[dict[str, Any]] = data if isinstance(data, list) else [data]
        if not rows:
            return self._send_json(400, {"ok": False, "error": "empty payload"})

        log = _today_log()
        existed = log.exists()
        written = 0
        try:
            with log.open("a", encoding="utf-8") as fh:
                for ev in rows:
                    if not isinstance(ev, dict):
                        continue
                    ev = _normalise(ev)
                    fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
                    written += 1
            if not existed:
                try:
                    os.chmod(log, 0o600)
                except OSError:
                    pass
        except OSError as exc:
            return self._send_json(500, {"ok": False, "error": f"write failed: {exc}"})

        self._send_json(200, {"ok": True, "written": written})


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    token = _ensure_token()
    RelayHandler.token = token
    server = ThreadingHTTPServer((host, port), RelayHandler)
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
