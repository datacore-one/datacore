"""Agent event emitter — append a row to the agent-stream JSONL.

Any agent (Data on Telegram, Tris researcher, Miles, nightshift, future
ones) can import this to push activity into the live agent stream that
the datacore-app daemon serves.

Pipeline:
  1. Agent calls emit(...).
  2. We append one JSON line to
     ~/.datacore/cos/agent-stream/events-YYYY-MM-DD.jsonl
     in the same shape AgentStream / Lens consumes.
  3. The daemon's JSONL file-watcher (services/agent_stream.py)
     picks up the append and publishes it on the /events WebSocket.
  4. Today panel + Org Flow Pulse + chat sidecar render it live.

Why this shape and not direct HTTP into the daemon:

  - Agents may run on different hosts (nightshift server, mobile,
    headless processes) where the local datacored isn't reachable.
  - Filesystem append is the lowest common denominator.
  - The JSONL format is identical to what Lens would emit. When Lens
    lands on a host, agents on that host can switch to the Lens HTTP
    capture endpoint and the stream shape stays the same.

No external dependencies. Pure stdlib so it runs in the leanest
agent processes (Telegram bot, cron-driven scripts) without bringing
in the full datacored package.
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
    try:
        EVENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now(tz=timezone.utc).date().isoformat()
        log_path = EVENT_LOG_DIR / f"events-{date_str}.jsonl"
        # Open with mode 0600 to match the daemon's expectations.
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
