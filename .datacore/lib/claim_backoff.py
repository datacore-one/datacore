"""Pause a host's claiming after a login or credit failure; report it once.

Infrastructure failures are not counted as attempts (ledger_claim: a sick host
must not dismiss good work), so a dead key used to be retried every 15 minutes
forever -- plur-claw, 2026-09-28..30, until leaked temp dirs filled its disk.
A login or usage limit is reported, then we wait: the first such failure sets
a per-actor block in the host's state dir and is reported once; later ticks
skip claiming (items stay queued) and probe once every PROBE_AFTER; the first
successful run clears it. Deleting the block file also clears it.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ops_markers import AUTH_FAILURE_MARKERS

PROBE_AFTER = timedelta(hours=6)

#: Provider phrases meaning "nothing will run until a person acts", beyond the
#: login markers ops_markers already lists. Rate limits are transient and stay
#: with the ordinary retry.
CREDIT_MARKERS = ("no credits remaining", "insufficient_quota", "quota exceeded",
                  "billing hard limit", "payment required")


def is_access_failure(detail: str) -> bool:
    low = (detail or "").lower()
    return any(m in low for m in (*AUTH_FAILURE_MARKERS, *CREDIT_MARKERS))


def _path(actor: str) -> Path:
    root = Path(os.environ.get("DATACORE_STATE") or Path.home() / ".datacore" / "state")
    return root / f"claim-access-block-{actor}.json"


def status(actor: str, now: datetime | None = None) -> tuple[bool, str | None]:
    """(paused, reason). A present but unreadable block keeps the host paused."""
    path = _path(actor)
    if not path.exists():
        return False, None
    now = now or datetime.now(timezone.utc)
    try:
        block = json.loads(path.read_text())
        last = datetime.fromisoformat(block["last_failure"])
    except (ValueError, KeyError, TypeError, OSError):
        return True, f"access block file {path} is unreadable; delete it to resume"
    if now - last >= PROBE_AFTER:
        return False, None  # due for one probe
    return True, (f"paused since {block.get('since')}: {block.get('detail', '')[:160]} "
                  f"-- next probe after {(last + PROBE_AFTER).isoformat(timespec='minutes')}; "
                  f"delete {path} to resume now")


def probing(actor: str) -> bool:
    """A block exists but its quiet interval is over: run one item, not a batch."""
    return _path(actor).exists()


def record_failure(actor: str, detail: str, now: datetime | None = None) -> bool:
    """Record an access failure. True only for a new block, i.e. report it."""
    path = _path(actor)
    now = now or datetime.now(timezone.utc)
    try:
        block = json.loads(path.read_text())
        new = False
    except (OSError, ValueError):
        block, new = {"since": now.isoformat(timespec="minutes")}, True
    block.update(last_failure=now.isoformat(), detail=detail[:300])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(block, indent=1))
    return new


def clear(actor: str) -> None:
    _path(actor).unlink(missing_ok=True)
