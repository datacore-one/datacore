#!/usr/bin/env python3
"""Sidecar tailer that observes runtime log files and POSTs derived events
to the agent-stream relay.

Usage:
    AGENT_STREAM_RELAY_URL=http://host:18891 \
    AGENT_STREAM_RELAY_TOKEN=xxx \
    AGENT_NAME=tris \
    MODE=hermes \
    GLOB=$HOME/.hermes/sessions/*.jsonl \
    STATE_FILE=$HOME/.datacore/agent-stream-tail/state.json \
    python3 agent_stream_tail.py

Modes:
    openclaw  — parses ~/.openclaw/agents/main/sessions/*.trajectory.jsonl
                emits one event per `model.completed` event (agent finished thinking)
    hermes    — parses ~/.hermes/sessions/*.jsonl
                emits one event per assistant turn (role == "assistant" with content)

The sidecar:
  * Polls the glob every POLL_INTERVAL seconds (default 3s)
  * Tracks per-file byte offsets in STATE_FILE so it survives restarts
  * Detects file rotation/truncation by comparing inode + size
  * Limits per-line parse memory (2 MiB cap)
  * POSTs JSON events to {RELAY}/events with bearer auth
  * On POST failure, drops the event (ephemeral; relay is the canonical sink)
  * Skips silently on missing optional fields — never crashes the host process

Designed to run as a low-privilege systemd user service.
"""

from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

# ─── Config ────────────────────────────────────────────────────────────────

RELAY_URL = os.environ.get("AGENT_STREAM_RELAY_URL", "").rstrip("/")
RELAY_TOKEN = os.environ.get("AGENT_STREAM_RELAY_TOKEN", "")
AGENT_NAME = os.environ.get("AGENT_NAME", "unknown")
MODE = os.environ.get("MODE", "").lower()
GLOB_PATTERN = os.environ.get("GLOB", "")
STATE_FILE = Path(
    os.environ.get(
        "STATE_FILE",
        os.path.expanduser("~/.datacore/agent-stream-tail/state.json"),
    )
)
POLL_INTERVAL = float(os.environ.get("POLL_INTERVAL", "3.0"))
MAX_LINE_BYTES = int(os.environ.get("MAX_LINE_BYTES", str(2 * 1024 * 1024)))
DEDUP_WINDOW = int(os.environ.get("DEDUP_WINDOW", "256"))

if not RELAY_URL:
    print("agent_stream_tail: AGENT_STREAM_RELAY_URL not set", file=sys.stderr)
    sys.exit(2)
if MODE not in ("openclaw", "hermes"):
    print(f"agent_stream_tail: MODE must be openclaw|hermes, got {MODE!r}", file=sys.stderr)
    sys.exit(2)
if not GLOB_PATTERN:
    print("agent_stream_tail: GLOB not set", file=sys.stderr)
    sys.exit(2)


# ─── State ─────────────────────────────────────────────────────────────────

def load_state() -> dict[str, Any]:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            pass
    return {"files": {}, "seen_ids": []}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, separators=(",", ":")))
    tmp.replace(STATE_FILE)


# ─── Relay client ──────────────────────────────────────────────────────────

def post_event(ev: dict[str, Any]) -> bool:
    try:
        req = urllib.request.Request(
            f"{RELAY_URL}/events",
            data=json.dumps(ev).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **(
                    {"Authorization": f"Bearer {RELAY_TOKEN}"}
                    if RELAY_TOKEN
                    else {}
                ),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=4) as resp:
            return 200 <= resp.status < 300
    except Exception as exc:
        print(f"agent_stream_tail: relay POST failed: {exc}", file=sys.stderr)
        return False


# ─── Parsers ───────────────────────────────────────────────────────────────

# Strip markdown / formatting noise, collapse whitespace, cap length.
_WS = re.compile(r"\s+")


def _summarise(text: str, *, limit: int = 220) -> str:
    if not text:
        return ""
    text = _WS.sub(" ", text).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


def parse_openclaw(line: bytes, file_id: str) -> dict[str, Any] | None:
    """Parse one line of an OpenClaw trajectory.jsonl file."""
    try:
        rec = json.loads(line)
    except Exception:
        return None
    if not isinstance(rec, dict):
        return None
    rec_type = rec.get("type")
    if rec_type != "model.completed":
        return None
    ts = rec.get("ts") or ""
    session_id = rec.get("sessionId") or ""
    model_id = rec.get("modelId") or "?"
    seq = rec.get("seq")
    summary = f"completed turn ({model_id})"
    return {
        "id": f"openclaw-{session_id}-{seq}-completed" if session_id else None,
        "type": "agent.message",
        "agent": AGENT_NAME,
        "ts": ts,
        "summary": summary,
        "details": {
            "session_id": session_id,
            "seq": seq,
            "model": model_id,
            "provider": rec.get("provider"),
            "source_file": file_id,
        },
    }


def parse_hermes(line: bytes, file_id: str) -> dict[str, Any] | None:
    """Parse one line of a Hermes Agent session jsonl file."""
    try:
        rec = json.loads(line)
    except Exception:
        return None
    if not isinstance(rec, dict):
        return None
    role = rec.get("role")
    if role != "assistant":
        return None
    content = rec.get("content") or ""
    finish = rec.get("finish_reason") or ""
    if not content and finish == "tool_calls":
        # tool-call-only turn — record as "thinking", concise
        tool_calls = rec.get("tool_calls") or []
        names = [
            ((tc.get("function") or {}).get("name") or "?")
            for tc in tool_calls
            if isinstance(tc, dict)
        ]
        summary = f"calling {', '.join(names[:3])}" if names else "tool call"
    else:
        summary = _summarise(content) or "(empty turn)"
    ts = rec.get("timestamp") or ""
    # Use timestamp + offset hash for ID stability
    file_basename = Path(file_id).name
    line_hash = hash(line) & 0xFFFFFFFF
    return {
        "id": f"hermes-{file_basename}-{ts}-{line_hash}",
        "type": "agent.message",
        "agent": AGENT_NAME,
        "ts": ts,
        "summary": summary,
        "details": {
            "session_file": file_basename,
            "finish_reason": finish,
            "has_tool_calls": bool(rec.get("tool_calls")),
        },
    }


PARSER = parse_openclaw if MODE == "openclaw" else parse_hermes


# ─── Tailer loop ───────────────────────────────────────────────────────────

def file_key(path: Path) -> str:
    try:
        st = path.stat()
        return f"{st.st_ino}:{st.st_dev}"
    except FileNotFoundError:
        return ""


def tail_file(path: Path, file_state: dict[str, Any], dedup: set[str]) -> int:
    """Read new bytes from `path` since last offset; emit events. Returns count."""
    key = file_key(path)
    if not key:
        return 0
    prev_key = file_state.get("key")
    prev_offset = int(file_state.get("offset", 0))
    if prev_key != key:
        # File rotated or new file
        prev_offset = 0
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return 0
    if size < prev_offset:
        # Truncation — restart from beginning
        prev_offset = 0
    if size == prev_offset:
        return 0

    emitted = 0
    try:
        with path.open("rb") as fh:
            fh.seek(prev_offset)
            buf = b""
            while True:
                chunk = fh.read(64 * 1024)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_LINE_BYTES * 4:
                    # Pathological: drop everything before the last newline
                    last_nl = buf.rfind(b"\n")
                    if last_nl > 0:
                        buf = buf[last_nl + 1:]
                    else:
                        buf = b""
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    if len(line) > MAX_LINE_BYTES:
                        continue
                    ev = PARSER(line, str(path))
                    if not ev:
                        continue
                    eid = ev.get("id")
                    if eid:
                        if eid in dedup:
                            continue
                        dedup.add(eid)
                    if post_event(ev):
                        emitted += 1
            new_offset = fh.tell() - len(buf)
    except OSError:
        return 0

    file_state["key"] = key
    file_state["offset"] = new_offset
    return emitted


def main() -> int:
    state = load_state()
    files = state.setdefault("files", {})
    seen_ids = state.setdefault("seen_ids", [])
    dedup = set(seen_ids)

    print(
        f"agent_stream_tail: agent={AGENT_NAME} mode={MODE} relay={RELAY_URL} "
        f"glob={GLOB_PATTERN} state={STATE_FILE}",
        file=sys.stderr,
        flush=True,
    )

    while True:
        try:
            paths = sorted(Path(p) for p in glob.glob(GLOB_PATTERN))
            for path in paths:
                fkey = str(path)
                fstate = files.setdefault(fkey, {})
                emitted = tail_file(path, fstate, dedup)
                if emitted:
                    print(
                        f"agent_stream_tail: emitted {emitted} events from {path.name}",
                        file=sys.stderr,
                        flush=True,
                    )
            # Cap dedup memory
            if len(dedup) > DEDUP_WINDOW:
                # Trim to most recent — preserve list ordering by keeping last N
                seen_ids = list(dedup)[-DEDUP_WINDOW:]
                dedup = set(seen_ids)
                state["seen_ids"] = seen_ids
            else:
                state["seen_ids"] = list(dedup)
            save_state(state)
        except Exception as exc:
            print(f"agent_stream_tail: loop error: {exc}", file=sys.stderr, flush=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
