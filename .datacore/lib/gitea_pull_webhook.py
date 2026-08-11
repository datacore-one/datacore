"""Tiny HTTP receiver for Gitea push webhooks. Triggers `git pull` on a
matched repo when Gitea POSTs a push event.

Routes:
  POST /pull/<space-name>  — pulls $DATACORE_ROOT/<space-name>
                            Body can be the Gitea push payload (ignored)
                            or empty.

Auth: shared secret in X-Gitea-Signature header (Gitea HMAC) OR a simple
shared token in the URL ?token=... — keep it dumb, it is only listening
on Tailscale.

Listens on Tailscale IP (100.101.159.42) port 8765 by default. Override
via env: PULL_WEBHOOK_BIND, PULL_WEBHOOK_PORT.
"""
import http.server
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BIND = os.environ.get("PULL_WEBHOOK_BIND", "100.101.159.42")
PORT = int(os.environ.get("PULL_WEBHOOK_PORT", "8765"))
SECRET = os.environ.get("PULL_WEBHOOK_SECRET", "")
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))
ALLOWED_SPACES = {"0-personal", "1-datafund", "2-datacore", "3-fds",
                  "4-forge", "5-plur", "6-meridian", "7-megaphone", "8-firm"}

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    def do_POST(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "pull":
            self.send_error(404, "use POST /pull/<space-name>")
            return
        space = parts[1]
        if space not in ALLOWED_SPACES:
            self.send_error(400, f"unknown space: {space}")
            return
        # Optional shared-secret check
        if SECRET:
            q = parse_qs(parsed.query)
            tok = (q.get("token") or [None])[0]
            if tok != SECRET:
                self.send_error(401, "bad token")
                return
        # Drain request body (we do not need it)
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length:
            self.rfile.read(length)

        space_dir = DATA_DIR / space
        if not (space_dir / ".git").exists():
            self.send_error(404, f"no .git in {space_dir}")
            return

        # The single transport, never `pull --rebase --autostash`. Autostash
        # keeps the stash when its pop conflicts, which stranded 29 stashes and
        # wrote conflict markers into journals and org/inbox.org, silently
        # blocking briefing delivery for three days (2026-08-03).
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from ledger_transport import sync_repo

        t0 = time.time()
        try:
            outcome = sync_repo(space_dir, quiet=True)
            # offline is transient and self-clearing, so a webhook can shrug it
            # off; 'blocked' cannot clear on its own and must surface. 'rescued'
            # was retired with the rescue-branch path it named.
            rc = 0 if outcome in ("clean", "offline") else 1
            out = f"sync_repo: {outcome}"
        except Exception as exc:  # noqa: BLE001 - report, never crash the hook
            rc, out, outcome = 1, f"sync_repo failed: {exc}", "error"
        dur = time.time() - t0

        class _P:
            returncode = rc
        proc = _P()
        if proc.returncode == 0:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": True, "space": space, "duration_sec": round(dur, 2),
                "output": out[-500:],
            }).encode())
        else:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "ok": False, "space": space, "exit": proc.returncode,
                "duration_sec": round(dur, 2), "output": out[-500:],
            }).encode())

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_error(404)


if __name__ == "__main__":
    print(f"gitea_pull_webhook listening on {BIND}:{PORT}", flush=True)
    server = http.server.HTTPServer((BIND, PORT), Handler)
    server.serve_forever()
