"""Headless verification: prove datacored's chat WS streams a real answer.

Works against either backend/deployment:

  # the cos-server box (defaults, unchanged)
  python3 ws_chat_test.py

  # a local datacored on the Mac (anthropic backend == `claude -p`)
  DATACORED_URL=http://127.0.0.1:$(cat ~/.datacore/app/datacored.port) \
  DATACORED_TOKEN_FILE=~/.datacore/app/datacored.token \
  python3 ws_chat_test.py "Who are you?"
"""
import asyncio
import json
import os
import sys
import urllib.request
from pathlib import Path

import websockets

TOKEN_FILE = Path(
    os.path.expanduser(
        os.environ.get("DATACORED_TOKEN_FILE", "/root/.datacore/app/datacored.token")
    )
)
TOKEN = TOKEN_FILE.read_text().strip()
BASE = os.environ.get("DATACORED_URL", "http://127.0.0.1:8787").rstrip("/")
WS_BASE = BASE.replace("https://", "wss://").replace("http://", "ws://")
PROMPT = sys.argv[1] if len(sys.argv) > 1 else "Who are you? Answer in one sentence."

req = urllib.request.Request(
    BASE + "/chat/sessions",
    data=json.dumps({"agent_slug": os.environ.get("AGENT_SLUG")} if os.environ.get("AGENT_SLUG") else {}).encode(),
    headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"},
    method="POST",
)
sid = json.load(urllib.request.urlopen(req))["id"]
print("session created:", sid)


async def go():
    uri = f"{WS_BASE}/chat/sessions/{sid}?token={TOKEN}"
    async with websockets.connect(uri, open_timeout=15) as ws:
        await ws.send(json.dumps({"role": "user", "content": PROMPT}))
        text = ""
        while True:
            ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=180))
            t = ev.get("type")
            if t == "init":
                print("init ok")
            elif t == "text_delta":
                text += ev.get("delta", "")
            elif t == "status":
                print("status:", ev.get("message", "")[:80])
            elif t == "completed":
                print("ANSWER:", (ev.get("result") or text).strip()[:400])
                return
            elif t == "error":
                print("ERROR:", ev.get("message"))
                return


asyncio.run(go())
