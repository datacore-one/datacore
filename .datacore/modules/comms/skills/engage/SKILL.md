---
name: engage
description: Launch Chrome-based X feed engagement agent — likes, follows, reply drafts via Telegram
user-invocable: true
---

# /engage — X Feed Engagement Agent

Launch the Chrome-based X feed engagement agent. Likes, follows, drafts replies,
and sends them to Telegram for approval. Runs in cycles until stopped.

## Prerequisites

- Chrome browser open with Claude in Chrome extension connected
- Logged into X.com as the target account (@FairDataSociety, @plur_ai, etc.)

## Execution

On `/engage`, immediately launch a background agent with the feed engagement
instructions from `feed-engage.md`.

### Step 1: Verify Chrome connection

```
Call tabs_context_mcp to check Chrome is connected.
If no tabs exist, create one and navigate to https://x.com/home.
```

### Step 2: Check reply queue

Before starting the feed cycle, check if there are approved replies waiting
to be posted on the **server** (where Telegram bot writes approvals):

```bash
ssh nightshift "cat Data/.datacore/state/reply-queue.jsonl 2>/dev/null"
```

If entries exist, post them first via Chrome (navigate to target URL, type reply,
click Reply button). Clear posted entries from the queue:

```bash
ssh nightshift "echo '' > Data/.datacore/state/reply-queue.jsonl"
```

### Step 3: Launch feed engagement agent

Launch as a **background agent** using the Agent tool with the full
`feed-engage.md` instructions. The agent runs autonomously:

- Scrolls the "For you" feed
- Likes tweets scoring 5+ relevance
- Follows accounts scoring 7+ relevance
- Drafts replies for tweets scoring 8+ (routes to Telegram via draft_pipeline)
- Drafts quote-RTs for tweets scoring 9+ with >50 likes

### Step 4: Report

After launching, report:
- Chrome connection status
- Number of queued replies posted (if any)
- Agent running in background
- How to check status: "engagement status" or MCP tools

## Reply Queue Processing

Approved replies are queued in `.datacore/state/reply-queue.jsonl` (JSONL format).
Each line contains:

```json
{
  "draft_id": "7d024582",
  "target_tweet_id": "2029058761134932107",
  "target_author": "@ZKArchitect",
  "target_url": "https://x.com/ZKArchitect/status/2029058761134932107",
  "reply_text": "Exactly. And when the infra itself can't read the data...",
  "approved_at": "2026-03-04T15:54:57+00:00"
}
```

To post via Chrome:
1. Navigate to `target_url`
2. Click the reply box
3. Type `reply_text`
4. Click Reply
5. Wait 3s, take screenshot to confirm
6. **Register in state** (see State Registration below)
7. Remove the entry from the queue file

## State Registration (MANDATORY)

**Every reply posted via Chrome MUST be registered in engagement-state.json.**
This enables cooldown tracking, analytics, and engagement improvement.

After EVERY successful reply post (whether from the reply queue, feed engagement,
or manual Chrome posting), run this on nightshift:

```bash
ssh nightshift "cd Data && python3 << 'PYEOF'
import sys, uuid; sys.path.insert(0, '.datacore/modules/comms/lib')
import engagement_state as s
from pathlib import Path
from datetime import datetime, timezone, timedelta
sf = Path('.datacore/state/engagement-state.json')
st, bl = s.load(sf)
now = datetime.now(timezone.utc)
st.setdefault('posted', []).append({
    'id': uuid.uuid4().hex[:8],
    'target_tweet_id': 'TWEET_ID',
    'target_author': '@AUTHOR',
    'target_content': 'TARGET_CONTENT'[:200],
    'target_url': 'TWEET_URL',
    'draft_reply': 'REPLY_TEXT',
    'our_tweet_id': 'OUR_TWEET_ID_OR_unknown',
    'posted_at': now.isoformat(),
    'analyze_at': (now + timedelta(hours=24)).isoformat(),
    'analyzed': False,
    'mode': 'autonomous',
    'source': 'chrome'
})
s.mark_seen(st, 'TWEET_ID')
s._bump_stat(st, 'posted')
s.save(st, sf, baseline=bl)
print('registered')
PYEOF"
```

Replace `TWEET_ID`, `@AUTHOR`, `TARGET_CONTENT`, `TWEET_URL`, `REPLY_TEXT`,
and `OUR_TWEET_ID_OR_unknown` with actual values.

**Before replying**, check cooldown:
```bash
ssh nightshift "cd Data && python3 -c \"
import sys; sys.path.insert(0, '.datacore/modules/comms/lib')
import engagement_state as s
from pathlib import Path
st, _ = s.load(Path('.datacore/state/engagement-state.json'))
print('skip' if s.recently_replied_to(st, '@AUTHOR', 168) else 'ok')
\""
```

If `skip` — do NOT reply to that author (7-day cooldown).

## Voice Guidelines

Reply voice follows VALIDATE -> BUILD -> GIFT:
1. Acknowledge what OP got right
2. Build on their point with a new angle
3. Gift them an insight that makes their argument stronger

Rules:
- 1-2 sentences max, under 120 chars ideal, 180 absolute max
- No links, no emojis, no hashtags
- Never negate or contradict OP
- One sharp point that BUILDS on OP

## Module

comms
