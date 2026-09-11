"""Authenticated Gitea push receiver: POST /pull/<numbered-space>.

Set PULL_WEBHOOK_SECRET. Gitea signs the exact body using X-Gitea-Signature
(hex HMAC-SHA256); other clients may use Authorization: Bearer <secret>.
URL tokens are rejected to keep credentials out of access logs and URLs.
The listener defaults to loopback; select an authorized private interface
or a TLS proxy explicitly with PULL_WEBHOOK_BIND / PULL_WEBHOOK_PORT.
"""
import hashlib
import hmac
import http.server
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

from http_utils import BoundedHTTPServer, RequestError, bearer_matches, read_body

BIND = os.environ.get("PULL_WEBHOOK_BIND", "127.0.0.1")
PORT = int(os.environ.get("PULL_WEBHOOK_PORT", "8765"))
SECRET = os.environ.get("PULL_WEBHOOK_SECRET", "")
DATA_DIR = Path(os.environ.get("DATACORE_ROOT", str(Path.home() / "Data")))


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        # BaseHTTPRequestHandler logs the full URL, including query secrets.
        return

    def do_POST(self):
        if not SECRET:
            self.send_error(503, "authentication is not configured")
            return
        parsed = urlparse(self.path)
        if parsed.query:
            self.send_error(400, "use header authentication")
            return
        parts = parsed.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "pull":
            self.send_error(404, "use POST /pull/<space-name>")
            return
        try:
            raw = read_body(self)
        except RequestError as exc:
            self.send_error(exc.status, str(exc))
            return
        signatures = self.headers.get_all("X-Gitea-Signature", [])
        if signatures:
            expected = hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()
            authenticated = len(signatures) == 1 and hmac.compare_digest(
                signatures[0].encode(), expected.encode())
        else:
            authenticated = bearer_matches(self.headers, SECRET)
        if not authenticated:
            self.send_error(401, "invalid authentication")
            return

        space = parts[1]
        if not re.fullmatch(r"[0-9]+-[a-zA-Z0-9][a-zA-Z0-9_-]*", space):
            self.send_error(400, "invalid space")
            return
        space_dir = DATA_DIR / space
        if space_dir.is_symlink() or space_dir.resolve().parent != DATA_DIR.resolve():
            self.send_error(400, "invalid space boundary")
            return
        if not (space_dir / ".git").exists():
            self.send_error(404, "space repository does not exist")
            return

        from ledger_transport import sync_repo
        started = time.monotonic()
        try:
            outcome = sync_repo(space_dir, quiet=True)
        except Exception:  # transport errors must not expose local paths or credentials
            outcome = "error"
        status = 200 if outcome == "clean" else 503 if outcome == "offline" else 500
        body = json.dumps({
            "ok": status == 200, "space": space,
            "duration_sec": round(time.monotonic() - started, 2),
            "output": f"sync_repo: {outcome}",
        }).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"ok\n")
        else:
            self.send_error(404)


if __name__ == "__main__":
    if not SECRET:
        raise SystemExit("PULL_WEBHOOK_SECRET must be configured")
    with BoundedHTTPServer((BIND, PORT), Handler) as server:
        print(f"gitea_pull_webhook listening on {BIND}:{PORT}", flush=True)
        server.serve_forever()
