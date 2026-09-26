"""Explicit, manual smoke check of an authorized datacored chat stream.

Importing this module never reads credentials, creates sessions, or calls a
model. Invoke with --base-url and --token-file against a test installation.
A completed stream verifies transport, not the provider's identity.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
from pathlib import Path
import time
import urllib.parse
import urllib.request


async def check_chat(base: str, token: str, agent: str) -> bool:
    import websockets

    request = urllib.request.Request(
        base + "/chat/sessions",
        data=json.dumps({"agent_slug": agent}).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        session_id = json.load(response)["id"]
    scheme = "wss" if base.startswith("https:") else "ws"
    uri = scheme + base[base.index(":"):] + "/chat/sessions/" + urllib.parse.quote(str(session_id), safe="")
    # websockets renamed this option when switching its default asyncio client.
    option = "additional_headers" if "additional_headers" in inspect.signature(websockets.connect).parameters else "extra_headers"
    async with websockets.connect(uri, open_timeout=15,
                                  **{option: {"Authorization": "Bearer " + token}}) as ws:
        await ws.send(json.dumps({"role": "user", "content": "Who are you? Answer in one sentence."}))
        deadline = time.monotonic() + 180
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("chat stream exceeded its deadline")
            event = json.loads(await asyncio.wait_for(ws.recv(), timeout=remaining))
            if event.get("type") == "completed":
                print("PASS: authenticated chat stream completed")
                return True
            if event.get("type") == "error":
                print("FAIL: chat stream returned an error")
                return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="authorized test daemon HTTP(S) URL")
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--agent", default="mr-data")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path:
        parser.error("base URL must be an HTTP(S) origin without credentials, path, query or fragment")
    try:
        token = args.token_file.read_text().strip()
        if not token:
            raise ValueError("empty credential")
        return 0 if asyncio.run(check_chat(base, token, args.agent)) else 1
    except (OSError, ValueError, ImportError, TimeoutError) as exc:
        # Exception text from transports can contain sensitive request details.
        print(f"FAIL: chat verification failed ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
