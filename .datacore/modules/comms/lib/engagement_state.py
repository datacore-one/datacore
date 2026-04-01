#!/usr/bin/env python3
"""Engagement pipeline state management.

Handles seen conversations, pending approvals, posted replies, daily stats.
State file: .datacore/state/engagement-state.json

Concurrency: Multiple processes (engine cron, draft pipeline, telegram bot,
Chrome agent) modify this state concurrently. save() uses file locking and
merge-on-write to prevent lost updates.
"""

import copy
import fcntl
import json
import os
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


STATE_FILE = Path("~/.datacore-state/engagement-state.json")  # overridden by callers

DATA_DIR = Path(os.environ.get("DATACORE_ROOT", os.path.expanduser("~/Data")))
ERRORS_FILE = DATA_DIR / ".datacore" / "state" / "engagement-errors.jsonl"
ERRORS_LOCK_FILE = DATA_DIR / ".datacore" / "state" / "engagement-errors.lock"
ERRORS_MAX = 500
ERRORS_TRIM_AT = 550


def _default_state():
    return {
        "config": {
            "max_per_hour": 5,
            "max_per_day": 15,
            "cooldown_hours": 168,  # 7 days — avoid replying to same author repeatedly
            "blacklist_authors": [],
            "query_rotation_index": 0,
        },
        "seen": {},
        "pending": [],
        "posted": [],
        "rejected": [],
        "daily_stats": {},
        "last_run": None,
        "_processed": [],
    }


def load(state_file: Path) -> tuple[dict, dict]:
    """Load state from disk.

    Returns: (state, baseline) where baseline is a deep copy of daily_stats
    at load time. Pass baseline to save() to enable delta-based merging that
    prevents lost increments when two processes write concurrently.

    Read-only callers can ignore the baseline and pass baseline=None to save().
    """
    if state_file.exists():
        state = json.loads(state_file.read_text())
    else:
        state = _default_state()
    baseline = copy.deepcopy(state.get("daily_stats", {}))
    return state, baseline


def save(state: dict, state_file: Path, baseline: dict = None):
    """Save state with file locking and merge-on-write.

    Reloads current disk state under an exclusive lock, merges in-memory
    changes, then writes. Prevents concurrent processes from overwriting
    each other's additions.

    Args:
        state: In-memory state dict to persist.
        state_file: Path to the state JSON file.
        baseline: The daily_stats snapshot from when load() was called.
            When provided, uses delta-based merge (disk + (mem - baseline))
            to preserve concurrent increments. When None, falls back to
            max()-based merge (safe for read-only callers that don't bump stats).
    """
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now(timezone.utc).isoformat()

    lock_file = state_file.with_suffix('.lock')
    with open(lock_file, 'w') as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            if state_file.exists():
                disk = json.loads(state_file.read_text())
                state = _merge(disk, state, baseline=baseline)
            state_file.write_text(json.dumps(state, indent=2))
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def _merge(disk: dict, mem: dict, baseline: dict = None) -> dict:
    """Merge in-memory state with on-disk state.

    Rules:
    - seen: union of both dicts
    - pending: union by id, excluding items in _processed
    - posted: union by id field (added to posted entries)
    - rejected: union by id field
    - config: mem wins
    - daily_stats: delta-based if baseline provided; max() fallback otherwise
    - _processed: union, trimmed to last 200

    daily_stats merge strategy:
      With baseline: disk_val + (mem_val - baseline_val)
        Preserves both callers' increments when two processes write concurrently.
      Without baseline (read-only callers): max(disk_val, mem_val)
        Safe because read-only callers don't bump stats.
    """
    result = dict(mem)

    # Merge seen (union)
    seen = dict(disk.get("seen", {}))
    seen.update(mem.get("seen", {}))
    result["seen"] = seen

    # Collect all processed IDs (approved/rejected by any process)
    processed = set(disk.get("_processed", []) + mem.get("_processed", []))

    # Merge pending: union by id, excluding processed items
    all_pending = _union_by_key(
        disk.get("pending", []), mem.get("pending", []), "id"
    )
    result["pending"] = [p for p in all_pending if p["id"] not in processed]

    # Merge posted: union by id
    result["posted"] = _union_by_key(
        disk.get("posted", []), mem.get("posted", []), "id"
    )

    # Merge rejected: union by id
    result["rejected"] = _union_by_key(
        disk.get("rejected", []), mem.get("rejected", []), "id"
    )

    # Merge daily stats
    disk_stats = disk.get("daily_stats", {})
    mem_stats = mem.get("daily_stats", {})
    merged_stats = {}
    all_days = set(list(disk_stats.keys()) + list(mem_stats.keys()))
    for day in all_days:
        merged_stats[day] = {}
        d = disk_stats.get(day, {})
        m = mem_stats.get(day, {})
        b = (baseline or {}).get(day, {}) if baseline is not None else None
        for k in set(list(d.keys()) + list(m.keys())):
            disk_val = d.get(k, 0)
            mem_val = m.get(k, 0)
            if b is not None:
                # Delta-based: preserve concurrent increments from both processes
                base_val = b.get(k, 0)
                delta = mem_val - base_val
                merged_stats[day][k] = disk_val + max(0, delta)
            else:
                # Max fallback: safe for read-only callers
                merged_stats[day][k] = max(disk_val, mem_val)
    result["daily_stats"] = merged_stats

    # Keep _processed trimmed (last 200 entries)
    result["_processed"] = list(processed)[-200:]

    return result


def _union_by_key(list_a: list, list_b: list, key: str) -> list:
    """Union two lists of dicts by key field. list_b wins on conflict."""
    seen_keys = {}
    for item in list_a:
        k = item.get(key)
        if k:
            seen_keys[k] = item
    for item in list_b:
        k = item.get(key)
        if k:
            seen_keys[k] = item
    return list(seen_keys.values())


def log_error(service: str, error_type: str, detail: str):
    """Append an error to the persistent error log with file locking.

    Entry format: {ts, service, error_type, detail}
    Trims to last ERRORS_MAX entries when count exceeds ERRORS_TRIM_AT.
    Uses a separate lock file to prevent JSONL corruption from concurrent writers.
    """
    ERRORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry = json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "error_type": error_type,
        "detail": str(detail)[:500],
    })

    with open(ERRORS_LOCK_FILE, "w") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            if ERRORS_FILE.exists():
                lines = [l for l in ERRORS_FILE.read_text().splitlines() if l.strip()]
            else:
                lines = []
            lines.append(entry)
            # Batch trim when over ERRORS_TRIM_AT to avoid read-modify-write on every write
            if len(lines) > ERRORS_TRIM_AT:
                lines = lines[-ERRORS_MAX:]
            ERRORS_FILE.write_text("\n".join(lines) + "\n")
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def is_seen(state: dict, tweet_id: str) -> bool:
    return tweet_id in state.get("seen", {})


def mark_seen(state: dict, tweet_id: str):
    state.setdefault("seen", {})[tweet_id] = datetime.now(timezone.utc).isoformat()


def add_pending(state: dict, target_tweet_id: str, target_author: str,
                target_content: str, target_url: str, draft_reply: str,
                telegram_message_id: int = None, account: str = 'fds') -> str:
    draft_id = str(uuid.uuid4())[:8]
    return add_pending_with_id(
        state, draft_id, target_tweet_id, target_author,
        target_content, target_url, draft_reply, telegram_message_id,
        account=account,
    )


def add_pending_with_id(state: dict, draft_id: str, target_tweet_id: str,
                        target_author: str, target_content: str,
                        target_url: str, draft_reply: str,
                        telegram_message_id: int = None,
                        account: str = 'fds') -> str:
    """Register a pending draft with a caller-specified ID."""
    state.setdefault("pending", []).append({
        "id": draft_id,
        "target_tweet_id": target_tweet_id,
        "target_author": target_author,
        "target_content_snippet": target_content[:120],
        "target_url": target_url,
        "draft_reply": draft_reply,
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "telegram_message_id": telegram_message_id,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(),
        "account": account,
    })
    return draft_id


def has_pending_for_tweet(state: dict, target_tweet_id: str) -> bool:
    """Return True if there's already a draft for this tweet (pending, analyzed, or queued)."""
    if not target_tweet_id or target_tweet_id in ('unknown', ''):
        return False

    # Check pending and analyzed drafts in state
    for key in ('pending', 'pending_drafts', 'analyzed_drafts'):
        if any(p.get('target_tweet_id') == target_tweet_id for p in state.get(key, [])):
            return True

    # Also check the reply queue file (approved but not yet posted)
    import json as _json
    import os as _os
    from pathlib import Path as _Path
    queue_file = _Path(_os.environ.get('DATACORE_ROOT', _os.path.expanduser('~/Data'))) / '.datacore' / 'state' / 'reply-queue.jsonl'
    if queue_file.exists():
        for line in queue_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = _json.loads(line)
                if entry.get('target_tweet_id') == target_tweet_id:
                    return True
            except Exception:
                pass

    return False


def get_pending(state: dict, draft_id: str) -> Optional[dict]:
    for p in state.get("pending", []):
        if p["id"] == draft_id:
            return p
    return None


def approve_pending(state: dict, draft_id: str, our_tweet_id: str) -> Optional[dict]:
    pending = state.get("pending", [])
    for i, p in enumerate(pending):
        if p["id"] == draft_id:
            item = pending.pop(i)
            state.setdefault("posted", []).append({
                "id": draft_id,
                "our_tweet_id": our_tweet_id,
                "target_tweet_id": item["target_tweet_id"],
                "target_author": item["target_author"],
                "draft_reply": item["draft_reply"],
                "posted_at": datetime.now(timezone.utc).isoformat(),
            })
            # Track so merge doesn't re-add to pending
            state.setdefault("_processed", []).append(draft_id)
            _bump_stat(state, "posted")
            return item
    return None


def reject_pending(state: dict, draft_id: str) -> Optional[dict]:
    pending = state.get("pending", [])
    for i, p in enumerate(pending):
        if p["id"] == draft_id:
            item = pending.pop(i)
            state.setdefault("rejected", []).append({
                **item,
                "rejected_at": datetime.now(timezone.utc).isoformat(),
            })
            # Track so merge doesn't re-add to pending
            state.setdefault("_processed", []).append(draft_id)
            _bump_stat(state, "rejected")
            return item
    return None


def expire_old_pending(state: dict) -> int:
    now = datetime.now(timezone.utc)
    pending = state.get("pending", [])
    expired = []
    remaining = []
    for p in pending:
        expires_at = p.get("expires_at")
        if not expires_at:
            remaining.append(p)
            continue
        exp = datetime.fromisoformat(expires_at)
        if now > exp:
            expired.append(p)
        else:
            remaining.append(p)
    state["pending"] = remaining
    for _ in expired:
        _bump_stat(state, "expired")
    return len(expired)


def today_count(state: dict, key: str) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return state.get("daily_stats", {}).get(today, {}).get(key, 0)


def _bump_stat(state: dict, key: str):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = state.setdefault("daily_stats", {}).setdefault(today, {})
    stats[key] = stats.get(key, 0) + 1


def recently_replied_to(state: dict, author: str, hours: int = 24) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for p in state.get("posted", []):
        if p.get("target_author") == author:
            posted_at = datetime.fromisoformat(p["posted_at"])
            if posted_at > cutoff:
                return True
    return False
