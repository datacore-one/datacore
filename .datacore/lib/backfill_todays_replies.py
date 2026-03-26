#!/usr/bin/env python3
"""Backfill today's Chrome-posted replies into engagement-state.json.

These replies were posted manually via Chrome MCP on 2026-03-12 but
weren't registered in engagement state because the manual workflow
bypassed the pipeline.
"""

import os
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import uuid

# Get absolute paths using DATACORE_ROOT
DATACORE_ROOT = Path(os.environ.get("DATACORE_ROOT", Path.home() / "Data"))
sys.path.insert(0, str(DATACORE_ROOT / ".datacore" / "modules" / "comms" / "lib"))
import engagement_state as s

STATE_FILE = DATACORE_ROOT / ".datacore" / "state" / "engagement-state.json"

# Today's confirmed replies from the Chrome session
REPLIES = [
    {
        "target_tweet_id": "2028512676570177934",
        "target_author": "@yrschrade",
        "target_content": "You can prove you're over 18 without revealing your name. ZK proofs make this possible today.",
        "target_url": "https://x.com/yrschrade/status/2028512676570177934",
        "draft_reply": "Most 'privacy-preserving' age checks still phone home to a central server. The architecture matters more than the policy.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T09:30:00+00:00",
    },
    {
        "target_tweet_id": "2026331487499370988",
        "target_author": "@LundukeJournal",
        "target_content": "Colorado age verification bill",
        "target_url": "https://x.com/LundukeJournal/status/2026331487499370988",
        "draft_reply": "Age verification that requires an ID upload is just surveillance with extra steps.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T09:45:00+00:00",
    },
    {
        "target_tweet_id": "unknown",
        "target_author": "@Togetherdec",
        "target_content": "Digital ID by stealth",
        "target_url": "https://x.com/Togetherdec",
        "draft_reply": "Digital ID by stealth. Every age gate is a login wall waiting to happen.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T10:00:00+00:00",
    },
    {
        "target_tweet_id": "2031822609164193822",
        "target_author": "@internetarchive",
        "target_content": "Internet Archive on building open systems",
        "target_url": "https://x.com/internetarchive/status/2031822609164193822",
        "draft_reply": "The next chapter is building systems where the architecture makes surveillance impossible, not just against policy.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T10:15:00+00:00",
    },
    {
        "target_tweet_id": "2031728580409876763",
        "target_author": "@juliecbarrett",
        "target_content": "Child safety legislation concerns",
        "target_url": "https://x.com/juliecbarrett/status/2031728580409876763",
        "draft_reply": "The pattern repeats: use child safety as the wrapper, build infrastructure that applies to everyone.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T10:30:00+00:00",
    },
    {
        "target_tweet_id": "2031763388418633931",
        "target_author": "@startpage",
        "target_content": "Metadata surveillance",
        "target_url": "https://x.com/startpage/status/2031763388418633931",
        "draft_reply": "Metadata is the content they pretend they're not reading.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T10:45:00+00:00",
    },
    {
        "target_tweet_id": "unknown",
        "target_author": "@jsrailton",
        "target_content": "Plaid data collection thread",
        "target_url": "https://x.com/jsrailton",
        "draft_reply": "Connect your bank once, get surveilled forever. Plaid is the quiet backbone of financial surveillance.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T11:00:00+00:00",
    },
    {
        "target_tweet_id": "unknown",
        "target_author": "@jsrailton",
        "target_content": "Signal encryption thread",
        "target_url": "https://x.com/jsrailton",
        "draft_reply": "The best encryption is the kind where even the people running the service can't comply with a subpoena.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T11:15:00+00:00",
    },
    {
        "target_tweet_id": "2026917129870524891",
        "target_author": "@subZraw",
        "target_content": "Chokepoint strategy discussion",
        "target_url": "https://x.com/subZraw/status/2026917129870524891",
        "draft_reply": "Chokepoint only works when there's a chokepoint. Fairdrop encrypts before upload — no server to subpoena.",
        "our_tweet_id": "unknown",
        "posted_at": "2026-03-12T11:30:00+00:00",
    },
]

def main():
    st, bl = s.load(STATE_FILE)
    now = datetime.now(timezone.utc)

    added = 0
    for r in REPLIES:
        entry_id = uuid.uuid4().hex[:8]
        posted_at = r["posted_at"]

        st.setdefault("posted", []).append({
            "id": entry_id,
            "target_tweet_id": r["target_tweet_id"],
            "target_author": r["target_author"],
            "target_content": r["target_content"][:200],
            "target_url": r["target_url"],
            "draft_reply": r["draft_reply"],
            "our_tweet_id": r["our_tweet_id"],
            "posted_at": posted_at,
            "analyze_at": (datetime.fromisoformat(posted_at) + timedelta(hours=24)).isoformat(),
            "analyzed": False,
            "mode": "autonomous",
            "source": "chrome",
        })

        # Mark tweet as seen
        if r["target_tweet_id"] != "unknown":
            s.mark_seen(st, r["target_tweet_id"])

        added += 1

    # Bump daily stats
    date_key = "2026-03-12"
    stats = st.setdefault("daily_stats", {}).setdefault(date_key, {})
    stats["posted"] = stats.get("posted", 0) + added

    s.save(st, STATE_FILE, baseline=bl)
    print(f"Backfilled {added} replies into engagement-state.json")
    print(f"Daily stats for {date_key}: posted={stats['posted']}")

if __name__ == "__main__":
    main()
