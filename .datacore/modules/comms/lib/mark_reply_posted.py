#!/usr/bin/env python3
"""Mark a Chrome-posted reply as done: update engagement state + remove from reply queue.

Usage (called by Chrome engagement agent after each successful post):
    python3 mark_reply_posted.py DRAFT_ID [OUR_TWEET_ID]

OUR_TWEET_ID is optional — use 'unknown' if not captured from the page.
"""

import json
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent.parent.parent  # ~/Data

STATE_FILE = DATA_DIR / ".datacore/state/engagement-state.json"
QUEUE_FILE = DATA_DIR / ".datacore/state/reply-queue.jsonl"
LOCK_FILE = STATE_FILE.with_suffix(".lock")


def mark_posted(draft_id: str, our_tweet_id: str = "unknown"):
    import fcntl
    from datetime import datetime, timezone, timedelta

    sys.path.insert(0, str(DATA_DIR / ".datacore/modules/comms/lib"))
    import engagement_state as state_mod

    # 1. Update engagement state: pending → posted
    lf = open(LOCK_FILE, "w")
    fcntl.flock(lf, fcntl.LOCK_EX)
    try:
        st, baseline = state_mod.load(STATE_FILE)
        result = state_mod.approve_pending(st, draft_id, our_tweet_id)
        if result:
            # Add analyze_at = 24h from now for metrics collection
            now = datetime.now(timezone.utc)
            for p in st.get("posted", []):
                if p["id"] == draft_id:
                    p["analyze_at"] = (now + timedelta(hours=24)).isoformat()
                    p["analyzed"] = False
                    break
            state_mod.save(st, STATE_FILE, baseline=baseline)
            print(f"State: marked {draft_id} as posted (analyze_at in 24h)")
        else:
            print(f"State: {draft_id} not in pending (may already be processed)")
    finally:
        fcntl.flock(lf, fcntl.LOCK_UN)
        lf.close()

    # 2. Remove from reply queue
    if QUEUE_FILE.exists():
        lines = [l for l in QUEUE_FILE.read_text().splitlines() if l.strip()]
        before = len(lines)
        lines = [l for l in lines if json.loads(l).get("draft_id") != draft_id]
        QUEUE_FILE.write_text("\n".join(lines) + ("\n" if lines else ""))
        removed = before - len(lines)
        print(f"Queue: removed {removed} entr{'y' if removed == 1 else 'ies'} ({len(lines)} remain)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: mark_reply_posted.py DRAFT_ID [OUR_TWEET_ID]")
        sys.exit(1)
    draft_id = sys.argv[1]
    our_tweet_id = sys.argv[2] if len(sys.argv) > 2 else "unknown"
    mark_posted(draft_id, our_tweet_id)
