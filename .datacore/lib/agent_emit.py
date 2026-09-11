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

If RELAY_URL is set, every event is first retained in a durable sibling
agent-stream-outbox directory. Confirmed relay acceptance removes it. New
successful sends retry older events; schedule `agent_emit.py --flush` to
retry while idle. Inbound sync must never replace the outbox directory.

Uses the shared Datacore file helpers and their core dependencies.
"""
from __future__ import annotations

import json
import os
import sys
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


_relay_fallback_warned = False


def _warn_relay_down(reason: str) -> None:
    """One warning per process when a configured relay stops accepting
    events. The durable outbox preserves the events, and the
    operator should learn the relay is down from the logs — not from
    noticing the stream went quiet days later."""
    global _relay_fallback_warned
    if not _relay_fallback_warned:
        print(f"[agent-stream] WARNING: relay unavailable ({reason}); "
              "event retained in the relay outbox", file=sys.stderr)
        _relay_fallback_warned = True


def _post_to_relay(ev: dict[str, Any]) -> bool:
    """Best-effort POST to the relay. Returns True on success, False on
    any error (network, status, parse). The durable outbox remains queued
    on False."""
    from relay_client import post_event
    ok = post_event(ev, _RELAY_URL, _RELAY_TOKEN, timeout=3)
    if not ok:
        _warn_relay_down("request or acknowledgement failed")
    return ok


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

    # 1) A configured relay has a separate durable outgoing queue.
    if _RELAY_URL:
        from agent_outbox import enqueue, acknowledge
        try:
            # Persist before sending, outside the inbound rsync directory.
            # Interrupted acknowledgement is an idempotent relay retry.
            enqueue(_outbox_dir(), ev)
            if _post_to_relay(ev):
                acknowledge(_outbox_dir(), ev)
                try:
                    flush_pending(limit=10)
                except (OSError, ValueError) as exc:
                    _warn_relay_down(type(exc).__name__)
            return ev
        except (OSError, ValueError):
            return {}

    # 2) Without a configured relay, publish to the local canonical stream.
    try:
        EVENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(tz=timezone.utc).date().isoformat()
        log_path = EVENT_LOG_DIR / f"events-{date_str}.jsonl"
        from agent_stream_store import append_events
        append_events(log_path, [ev])
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
    event_id = None
    if message_id:
        import hashlib
        # Message identifiers are scoped to a conversation/account, not global.
        scope = (details or {}).get("chat_id") or recipient
        if scope is not None:
            identity = json.dumps([agent, channel, str(scope), str(message_id)], separators=(",", ":"))
            event_id = "message-" + hashlib.sha256(identity.encode()).hexdigest()
    return emit(
        event_type=f"{channel}.message",
        agent=agent,
        summary=summary,
        severity="info",
        details=extra,
        event_id=event_id,
    )


def _outbox_dir():
    return EVENT_LOG_DIR.with_name(EVENT_LOG_DIR.name + '-outbox')


def flush_pending(limit=100):
    """Retry retained relay events; successful sends alone remove queued data."""
    if not _RELAY_URL:
        raise ValueError('relay URL is not configured')
    from agent_outbox import flush
    return flush(_outbox_dir(), _post_to_relay, limit=limit)


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
    if '--flush' in sys.argv[1:]:
        sent = flush_pending()
        remaining = len(list(_outbox_dir().glob('*.json')))
        print(json.dumps({'sent': sent, 'remaining': remaining}))
        raise SystemExit(1 if remaining else 0)
    # Smoke test from the CLI:
    #   python3 ~/Data/.datacore/lib/agent_emit.py
    out = emit(
        "agent.test",
        agent="cli-test",
        summary="agent_emit.py smoke test",
        severity="info",
    )
    print(json.dumps(out, indent=2))
