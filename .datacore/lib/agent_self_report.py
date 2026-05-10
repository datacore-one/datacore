"""Crew agent self-report — write per-agent state for the Firm Status panel.

Sibling to agent_emit.py. Where agent_emit pushes ephemeral activity events
into the agent stream, agent_self_report writes the *current state* of a
crew member (Mr Data, Tris, Miles) to a JSON file the desktop app reads.

File path:
    ~/Data/.datacore/state/agents/{slug}.json

Schema (verbatim, the daemon parses these field names):
    {
        "name": "Mr Data",
        "last_activity": "2026-05-10T08:30:00Z",
        "last_status": "ok",
        "last_error": null,
        "last_summary": "Synthesised three CoS briefings; 1 escalation surfaced"
    }

Field semantics:
    name           — display name (matches the identity .md)
    last_activity  — ISO Z timestamp of most recent output
    last_status    — "ok" | "blocked" | "error" — anything else falls back to "ok"
    last_error     — free-text reason when status != ok; null otherwise
    last_summary   — short string the dashboard shows next to the agent name

Usage from a crew runtime:

    from agent_self_report import write_self_report
    write_self_report("data", "Mr Data", "ok",
                      summary="Synthesised three CoS briefings; 1 escalation surfaced")

Or after a failure:

    write_self_report("miles", "Miles", "error",
                      error="systemctl restart failed: timeout")

The function is best-effort — filesystem errors are swallowed so the agent's
primary work never breaks because of the dashboard hook.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Crew state lives under ~/Data/.datacore/state/agents/
# Override via env var DATACORE_AGENT_STATE_DIR for testing or relocation.
_AGENT_STATE_DIR = Path(
    os.environ.get(
        "DATACORE_AGENT_STATE_DIR",
        str(Path.home() / "Data" / ".datacore" / "state" / "agents"),
    )
)

_VALID_STATUSES = {"ok", "blocked", "error"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_self_report(
    slug: str,
    name: str,
    status: str = "ok",
    summary: str = "",
    error: Optional[str] = None,
    extra: Optional[dict] = None,
) -> Path:
    """Write the crew member's current state to {state_dir}/{slug}.json.

    Atomic via tempfile+rename so the dashboard never reads a partial JSON.
    Returns the path written. On error, returns the intended path silently
    (does not raise).

    Args:
        slug: lowercase identifier matching .datacore/agents/firm/{slug}.md
              (e.g. "data", "tris", "miles")
        name: display name shown in the dashboard
        status: "ok" | "blocked" | "error"
        summary: short string (≤200 chars recommended) for the dashboard
        error: optional error message (only meaningful when status != "ok")
        extra: optional dict of additional fields (preserved verbatim)
    """
    if status not in _VALID_STATUSES:
        status = "ok"

    target = _AGENT_STATE_DIR / f"{slug}.json"
    payload = {
        "name": name,
        "last_activity": _now_iso(),
        "last_status": status,
        "last_error": error if status != "ok" else None,
        "last_summary": str(summary)[:300],
    }
    if extra:
        payload.update({k: v for k, v in extra.items() if k not in payload})

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
            f.write("\n")
        tmp.replace(target)
    except OSError:
        pass  # best-effort

    return target


def read_self_report(slug: str) -> Optional[dict]:
    """Read the crew member's current state. Returns None if missing/unreadable."""
    target = _AGENT_STATE_DIR / f"{slug}.json"
    if not target.exists():
        return None
    try:
        with open(target) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


if __name__ == "__main__":
    # Smoke test:
    #   python3 ~/Data/.datacore/lib/agent_self_report.py
    out = write_self_report(
        "test-crew",
        "Test Crew Member",
        status="ok",
        summary="agent_self_report.py smoke test",
    )
    print(f"wrote: {out}")
    back = read_self_report("test-crew")
    print(f"read back: {json.dumps(back, indent=2)}")
    # cleanup
    out.unlink(missing_ok=True)
