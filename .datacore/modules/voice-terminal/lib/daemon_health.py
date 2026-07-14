#!/usr/bin/env python3
"""
Nightshift daemon health endpoint — M4 remote-daemon connect support.

Serves a minimal HTTP API so the datacore-app's install wizard can
probe the remote nightshift server and confirm connectivity/auth.

Usage (server-side, typically started by systemd or manually):
    python3 daemon_health.py [--port 8899] [--bind 127.0.0.1]

Endpoints:
    GET  /daemon/health          — unauthenticated ping (returns 200 + JSON)
    GET  /daemon/status          — authenticated; returns last TTS run, Telegram status
    POST /daemon/test-telegram   — authenticated; sends a smoke-test Telegram message

Auth:
    Bearer token from DAEMON_AUTH_TOKEN env var.
    If not set, auth is disabled (useful on tailnet where network-layer auth suffices).

The auth token is set in ~/config/nightshift.env as DAEMON_AUTH_TOKEN=<secret>.
The datacore-app stores it in ~/.datacore/app/remote.json after the install wizard.
"""

import json
import os
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


DATA_DIR = Path(os.environ.get("DATA_DIR", Path.home() / "Data"))
JOURNAL_DIR = DATA_DIR / "0-personal" / "notes" / "journals"
ENV_DIR = DATA_DIR / ".datacore" / "env"


def _load_auth_token() -> str:
    """Load DAEMON_AUTH_TOKEN from env or env files."""
    token = os.environ.get("DAEMON_AUTH_TOKEN", "")
    if token:
        return token
    for env_file in [ENV_DIR / ".env", ENV_DIR / "local.env",
                     Path.home() / "config" / "nightshift.env"]:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("DAEMON_AUTH_TOKEN="):
                    return line.split("=", 1)[1].strip()
    return ""


def _load_telegram_token() -> str:
    """Check if Telegram credentials are configured."""
    for key in ("TELEGRAM_BOT_TOKEN",):
        val = os.environ.get(key, "")
        if val:
            return val
    for env_file in [ENV_DIR / ".env", ENV_DIR / "local.env",
                     Path.home() / "config" / "nightshift.env"]:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("TELEGRAM_BOT_TOKEN="):
                    return line.split("=", 1)[1].strip()
    return ""


def _last_tts_run() -> dict:
    """Find today's or yesterday's spoken.txt and return metadata."""
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                 .astimezone(timezone.utc)
                 .__class__
                 .fromtimestamp(
                     datetime.now().timestamp() - 86400
                 ).strftime("%Y-%m-%d"))
    for date_str in [today, yesterday]:
        p = JOURNAL_DIR / f"{date_str}_spoken.txt"
        if p.exists():
            stat = p.stat()
            return {
                "date": date_str,
                "path": str(p),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                               .isoformat(),
            }
    return {"date": None, "path": None, "size_bytes": 0, "modified_at": None}


AUTH_TOKEN = _load_auth_token()


class HealthHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # Suppress default access log noise; use structured output instead
        print(f"[daemon_health] {self.address_string()} {fmt % args}", flush=True)

    def _check_auth(self) -> bool:
        """Return True if auth passes (or no token configured)."""
        if not AUTH_TOKEN:
            return True  # open — rely on network-layer auth (tailnet, SSH port-forward)
        header = self.headers.get("Authorization", "")
        return header == f"Bearer {AUTH_TOKEN}"

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, code: int, message: str):
        self._send_json(code, {"error": message, "code": code})

    def do_GET(self):
        if self.path == "/daemon/health":
            # Unauthenticated — app wizard uses this to confirm the server is reachable
            self._send_json(200, {
                "ok": True,
                "service": "nightshift-daemon",
                "version": "1.0.0",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "telegram_configured": bool(_load_telegram_token()),
            })

        elif self.path == "/daemon/status":
            if not self._check_auth():
                self._send_error(401, "unauthorized")
                return
            tts = _last_tts_run()
            self._send_json(200, {
                "ok": True,
                "service": "nightshift-daemon",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "telegram_configured": bool(_load_telegram_token()),
                "last_tts_run": tts,
                "data_dir": str(DATA_DIR),
            })

        else:
            self._send_error(404, f"not found: {self.path}")

    def do_POST(self):
        if self.path == "/daemon/test-telegram":
            if not self._check_auth():
                self._send_error(401, "unauthorized")
                return

            # Send a smoke-test message via Telegram
            import urllib.request, urllib.parse
            token = _load_telegram_token()
            chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
            if not chat_id:
                # Check env files
                for env_file in [ENV_DIR / ".env", ENV_DIR / "local.env",
                                  Path.home() / "config" / "nightshift.env"]:
                    if env_file.exists():
                        for line in env_file.read_text().splitlines():
                            if line.startswith("TELEGRAM_CHAT_ID="):
                                chat_id = line.split("=", 1)[1].strip()
                                break

            if not token or not chat_id:
                self._send_json(503, {
                    "ok": False,
                    "error": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured",
                })
                return

            try:
                text = "Hello from Winston. Telegram is connected to your nightshift daemon."
                data = urllib.parse.urlencode({
                    "chat_id": chat_id,
                    "text": text,
                }).encode()
                req = urllib.request.Request(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    data=data,
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read())
                if result.get("ok"):
                    self._send_json(200, {"ok": True, "message": "Test message sent to Telegram."})
                else:
                    self._send_json(502, {"ok": False, "error": str(result)})
            except Exception as e:
                self._send_json(502, {"ok": False, "error": str(e)})

        else:
            self._send_error(404, f"not found: {self.path}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Nightshift daemon health endpoint")
    parser.add_argument("--port", type=int, default=8899, help="Port to bind (default: 8899)")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="Interface to bind (default: 127.0.0.1). Use 0.0.0.0 for network access.")
    args = parser.parse_args()

    if AUTH_TOKEN:
        print(f"[daemon_health] Auth: DAEMON_AUTH_TOKEN configured ({len(AUTH_TOKEN)} chars)")
    else:
        print("[daemon_health] Auth: OPEN (no DAEMON_AUTH_TOKEN set — use network-layer auth)")

    server = HTTPServer((args.bind, args.port), HealthHandler)
    print(f"[daemon_health] Listening on {args.bind}:{args.port}")
    print(f"[daemon_health] Health:  http://{args.bind}:{args.port}/daemon/health")
    print(f"[daemon_health] Status:  http://{args.bind}:{args.port}/daemon/status")
    print(f"[daemon_health] Telegram test:  POST http://{args.bind}:{args.port}/daemon/test-telegram")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[daemon_health] Shutting down.")


if __name__ == "__main__":
    main()
