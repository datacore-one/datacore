"""Agent event emitter — push activity rows to the agent stream.

Any agent (Data on Telegram, Tris researcher, Miles, nightshift, future
ones) can import this to push activity into the live agent stream that
the datacore-app daemon serves on the Mac.

Two transport modes, picked at runtime:

  1. **HTTP relay mode** (preferred for cross-host agents) — when env var
     ``AGENT_STREAM_RELAY_URL`` is set, POST the event to that URL.
     Use this for agents running on nightshift, on a Pi, anywhere
     except where the Mac frontend lives. The relay
     (lib/agent_stream_relay.py) writes the canonical JSONL store on
     its always-on host. Mac syncs the JSONL via rsync; its existing
     JsonlAppendWatcher picks up the changes.

  2. **Local file mode** (default) — append to
     ``~/.datacore/cos/agent-stream/events-YYYY-MM-DD.jsonl`` directly.
     Use this when the agent runs on the same host as datacored.

The event shape is identical in both modes — when Lens lands later,
the call site flips to Lens's capture endpoint without changing
either consumer (relay or local watcher).

Configuration
=============

Two env vars (both optional):

  AGENT_STREAM_RELAY_URL   e.g. http://nightshift:18891
  AGENT_STREAM_RELAY_TOKEN bearer token for the relay (read once,
                            cached for process lifetime)

If RELAY_URL is set, transport flips to HTTP. If the POST fails
(network blip, relay down), we fall back to local file append so
the event isn't lost — it'll be picked up by rsync next cycle.

No external dependencies. Pure stdlib so it runs in the leanest
agent processes (Telegram bot, cron-driven scripts).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVENT_LOG_DIR = Path.home() / ".datacore" / "cos" / "agent-stream"

_VALID_SEVERITIES = {"info", "success", "warning", "error"}

# Transport selection. Read once on import; set the env vars in the
# agent's environment (systemd EnvironmentFile, .env, shell rc).
_RELAY_URL = os.environ.get("AGENT_STREAM_RELAY_URL", "").rstrip("/")
_RELAY_TOKEN = os.environ.get("AGENT_STREAM_RELAY_TOKEN", "")


def _post_to_relay(ev: dict[str, Any]) -> bool:
    """Best-effort POST to the relay. Returns True on success, False on
    any error (network, status, parse). Caller falls back to local file
    append on False, so we never silently lose an event."""
    if not _RELAY_URL:
        return False
    try:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            _RELAY_URL + "/events",
            data=json.dumps(ev).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {_RELAY_TOKEN}"} if _RELAY_TOKEN else {}),
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
    except Exception:
        return False


def emit(
    event_type: str,
    agent: str,
    summary: str,
    severity: str = "info",
    details: dict[str, Any] | None = None,
    *,
    event_id: str | None = None,
) -> dict[str, Any]:
    """Append one event to today's JSONL and return the dict that was written.

    Args:
      event_type: dotted, e.g. "agent.message", "agent.task.completed",
                  "telegram.reply", "tris.research.finding".
      agent: bot/agent name. Convention: lowercase, e.g. "data", "tris",
             "miles", "data-tg".
      summary: one-line human summary, ≤120 chars recommended.
      severity: info | success | warning | error. Anything else → info.
      details: optional dict of structured context (sha, channel, …).
      event_id: pass an idempotent id if you want re-emits to dedup
                (e.g. f"telegram-{message_id}"). Otherwise we mint a uuid.

    Best-effort: filesystem errors are swallowed and an empty dict is
    returned. The agent's primary work shouldn't break because the
    activity log is read-only.
    """
    if severity not in _VALID_SEVERITIES:
        severity = "info"
    ev = {
        "id": event_id or uuid.uuid4().hex,
        "ts": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "type": event_type,
        "agent": agent,
        "summary": str(summary)[:300],
        "severity": severity,
        "details": details or None,
    }

    # 1) Try the relay if configured. If it works, we're done — the
    # canonical store on nightshift gets the row immediately, rsync ships
    # it to the Mac on the next cycle, the Mac watcher fires.
    if _RELAY_URL and _post_to_relay(ev):
        return ev

    # 2) Fall back to local file append. Either no relay configured (the
    # agent runs on the same host as datacored), or the relay was
    # unreachable (network blip — at least we keep the event locally).
    try:
        EVENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(tz=timezone.utc).date().isoformat()
        log_path = EVENT_LOG_DIR / f"events-{date_str}.jsonl"
        existed = log_path.exists()
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        if not existed:
            try:
                os.chmod(log_path, 0o600)
            except OSError:
                pass
        return ev
    except Exception:
        return {}


def emit_message(
    agent: str,
    channel: str,
    summary: str,
    *,
    recipient: str | None = None,
    message_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for outbound messages from an agent.

    Examples:
        emit_message("data", "telegram", "Replied to @gregor: ...",
                     recipient="@gregor", message_id="123")
        emit_message("data", "telegram", "Posted journal summary",
                     details={"chat_id": -100, "tokens": 250})
    """
    extra = {"channel": channel}
    if recipient:
        extra["recipient"] = recipient
    if message_id:
        extra["message_id"] = message_id
    if details:
        extra.update(details)
    return emit(
        event_type=f"{channel}.message",
        agent=agent,
        summary=summary,
        severity="info",
        details=extra,
        event_id=f"{channel}-{message_id}" if message_id else None,
    )


def emit_task(
    agent: str,
    task: str,
    status: str,
    *,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for task lifecycle events.

    Status maps to event type and severity:
        started   → agent.task.started   · info
        completed → agent.task.completed · success
        failed    → agent.task.failed    · error
        skipped   → agent.task.skipped   · info
    """
    s = (status or "").lower()
    type_map = {
        "started": ("agent.task.started", "info"),
        "completed": ("agent.task.completed", "success"),
        "failed": ("agent.task.failed", "error"),
        "skipped": ("agent.task.skipped", "info"),
    }
    event_type, severity = type_map.get(s, ("agent.task.event", "info"))
    return emit(
        event_type=event_type,
        agent=agent,
        summary=summary or f"{s}: {task}",
        severity=severity,
        details={"task": task, **(details or {})},
        event_id=f"task-{task_id}-{s}" if task_id else None,
    )


if __name__ == "__main__":
    # Smoke test from the CLI:
    #   python3 ~/Data/.datacore/lib/agent_emit.py
    out = emit(
        "agent.test",
        agent="cli-test",
        summary="agent_emit.py smoke test",
        severity="info",
    )
    print(json.dumps(out, indent=2))
