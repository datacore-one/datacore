# Feed Engagement — Chrome-Based Organic Activity

Run as a background agent on the local machine. Uses Chrome browser automation to
scroll the X "For you" feed, like relevant content, follow interesting accounts,
draft replies, and retweet exceptional content.

## Trigger

Conversational: "start feed engagement", "browse the feed", "engage on X"

## Prerequisites

- Chrome browser open with Claude extension connected
- Logged into X.com as the target account (@FairDataSociety, @plur_ai, etc.) — the engagement agent uses whichever account is logged in on Chrome

## Execution

**IMPORTANT**: Run this as a background agent using the Agent tool. The agent should
loop through feed scan cycles with 20-30 minute pauses between cycles.

### One Feed Scan Cycle

**Phase 1: Open Feed**
1. Call `tabs_context_mcp` to get current tabs
2. Find a tab on x.com, or create a new tab and navigate to `https://x.com/home`
3. Wait 3 seconds for feed to load
4. Take a screenshot to verify the feed loaded

**Phase 2: Scroll and Collect**
1. Read the page to find tweet elements
2. For each visible tweet, extract: author, content text, like count, reply count, retweet count
3. Scroll down 3-4 times, collecting tweets each time
4. Target: collect 20-30 tweets per cycle
5. Score each tweet for relevance (see Relevance Scoring below)

**Phase 3: Like (generous)**
For tweets scoring 5+/10 relevance:
1. Find the like button for that tweet
2. Click it
3. Wait 2-3 seconds between likes
4. Budget: up to 30 likes per cycle

**Phase 4: Follow (selective)**
For accounts that:
- Posted something scoring 7+/10 relevance
- Have between 500 and 100K followers
- Are not already followed
1. Click through to their profile
2. Click Follow
3. Navigate back to feed
4. Budget: up to 10 follows per cycle

**Phase 5: Reply (high-value, posts via Chrome)**
For tweets scoring 8+/10 relevance AND from accounts with >1K followers:

1. **Author cooldown check** — skip if replied to this author in last 7 days:
   ```bash
   ssh nightshift "cd Data && python3 -c \"
   import sys; sys.path.insert(0, '.datacore/modules/comms/lib')
   import engagement_state as s
   st, _ = s.load('.datacore/state/engagement-state.json')
   print('skip' if s.recently_replied_to(st, '@AUTHOR', 168) else 'ok')
   \""
   ```
   If `skip` → move to next tweet.

2. **Get full tweet text** — navigate to tweet URL, use `get_page_text`. Note the tweet ID from URL.

3. **Draft a reply** — follow voice guidelines below.

4. **Evaluate the draft:**
   ```bash
   ssh nightshift "cd Data && python3 -c \"
   import sys; sys.path.insert(0, '.datacore/modules/comms/lib')
   from draft_evaluator import evaluate_draft
   ev = evaluate_draft('REPLY TEXT', '@AUTHOR', 'TWEET CONTENT')
   print(ev.decision, f'{ev.consensus:.2f}')
   \""
   ```

5. **Act on result:**
   - **approved (≥0.65)**: Post directly via Chrome:
     1. Navigate to tweet URL
     2. Click reply box, type the reply, click Post
     3. Wait 3s, screenshot to confirm
     4. Register in state:
        ```bash
        ssh nightshift "cd Data && python3 << 'PYEOF'
        import sys, uuid; sys.path.insert(0, '.datacore/modules/comms/lib')
        import engagement_state as s
        from datetime import datetime, timezone, timedelta
        sf = '.datacore/state/engagement-state.json'
        st, bl = s.load(sf)
        now = datetime.now(timezone.utc)
        st.setdefault('posted', []).append({'id': uuid.uuid4().hex[:8], 'target_tweet_id': 'TWEET_ID', 'target_author': '@AUTHOR', 'target_content': 'CONTENT'[:200], 'target_url': 'URL', 'draft_reply': 'REPLY TEXT', 'our_tweet_id': 'OUR_ID_OR_unknown', 'posted_at': now.isoformat(), 'analyze_at': (now+timedelta(hours=24)).isoformat(), 'analyzed': False, 'mode': 'autonomous', 'source': 'chrome'})
        s.mark_seen(st, 'TWEET_ID')
        s._bump_stat(st, 'posted')
        s.save(st, sf, baseline=bl)
        print('registered')
        PYEOF"
        ```
   - **borderline (0.50–0.65)**: Send to Telegram:
     ```bash
     ssh nightshift "cd Data && python3 -c \"
     import sys; sys.path.insert(0, '.datacore/modules/comms/lib')
     from draft_pipeline import process_draft_pipeline
     r = process_draft_pipeline('REPLY', '@AUTHOR', 'CONTENT', 'URL', 'TWEET_ID', source='chrome')
     print(r['action'])
     \""
     ```
   - **rejected (<0.50)**: Skip, move on.

6. Budget: up to 5 reply drafts per cycle

**Phase 6: Retweet/Quote (exceptional only)**
For tweets scoring 9+/10 relevance AND >50 likes:
1. Draft a quote-RT with a short take (under 100 chars)
2. Send to Telegram for approval
3. Budget: up to 2 quote-RT drafts per cycle

### Relevance Scoring (0-10)

Score each tweet based on keyword matches and context:

**+3 points** (core topics):
- Privacy architecture, data sovereignty, self-sovereign
- Zero-knowledge, end-to-end encryption, decentralized storage
- Fair data, data rights, digital sovereignty

**+2 points** (adjacent topics):
- Surveillance, mass data collection, metadata tracking
- AI agents + privacy, MCP servers, agentic AI
- File sharing alternatives, WeTransfer/Dropbox complaints
- GDPR, data breaches, privacy regulation

**+1 point** (related):
- Ethereum ecosystem, Swarm network, public goods
- Open source, cypherpunk, digital rights
- Web3 builders (non-token, non-hype)

**-2 points** (noise):
- Token price, airdrop, WAGMI, moon language
- Engagement farming ("Good morning")
- Political hot takes without tech substance
- Celebrity/entertainment

**Minimum thresholds**:
- Like: 5+ score
- Follow: 7+ score
- Reply draft: 8+ score, >1K followers
- Quote-RT draft: 9+ score, >50 likes

### Voice Guidelines for Replies

You are adding to someone's conversation, not broadcasting. Sound like a person.

**Anti-smugness rules (these get rejected by evaluators):**
- No "the step most miss:", "most people don't realize" — condescending
- No "X isn't a feature — it's the layer that..." — asserting authority
- No "the real fix is..." — implies knowing better than OP
- Don't use the same opener structure every time ("Spot on.", "Exactly.") — formulaic

**Pick ONE reply type per tweet:**
- Simple agreement: when OP's point stands alone, just amplify it (can be very short)
- Extension: add one new angle, don't make it about FDS
- Question: genuine curiosity that extends the thread
- Experience: brief first-person from building privacy infra

Example good replies (varied structure):
- "This."
- "Policy changes. Architecture doesn't."
- "What's the hardest part — getting regulators to accept ZK proof for age-gating?"
- "We hit the same wall building Fairdrop. Encrypt-before-upload changed what we could promise."
- "And if the company gets acquired, that policy goes with it."

### Timing

- One cycle takes ~5-10 minutes
- Pause 20-30 minutes between cycles
- Run 3-4 cycles per hour
- Run continuously until Chrome disconnects or user stops — NO cycle limit

### Reply Queue Polling (EVERY cycle)

At the START of every cycle (before scrolling the feed), check for approved replies:

```bash
ssh nightshift "cat Data/.datacore/state/reply-queue.jsonl 2>/dev/null"
```

If entries exist:
1. For each entry: navigate to `target_url`, click reply box, type `reply_text`, click Reply
2. Wait 3s, screenshot to confirm post sent
3. **Capture our tweet ID**: after posting, look at the URL of our new reply in the thread or extract the tweet ID from any link that appears. The tweet ID is the number at the end of the tweet URL (e.g. `https://x.com/FairDataSociety/status/1234567890` → ID is `1234567890`). If you can't capture it, use `unknown`.
4. **After each successful post**, immediately mark it done (updates state + removes from queue):
   ```bash
   ssh nightshift "cd Data && python3 .datacore/modules/comms/lib/mark_reply_posted.py DRAFT_ID OUR_TWEET_ID"
   ```
   Replace `DRAFT_ID` with the `draft_id` field and `OUR_TWEET_ID` with the captured tweet ID (or `unknown`).
5. If a post fails (tweet deleted, replies restricted, etc.), skip it and still mark it done to avoid retrying forever.

Do NOT bulk-clear the queue at the end — remove entries one by one as you post them.

This ensures approved Telegram replies get posted automatically within the next cycle.

### State Tracking

Track what was liked/followed/drafted in this session to avoid duplicates.
Use a simple in-memory set of tweet IDs seen this session.

### Error Handling

- If Chrome disconnects, stop and notify user
- If X shows rate limit warning, pause 15 minutes
- If login required, stop and notify user
- Don't retry failed actions more than twice

## Environment

Uses Chrome MCP tools:
- `mcp__claude-in-chrome__tabs_context_mcp`
- `mcp__claude-in-chrome__navigate`
- `mcp__claude-in-chrome__computer` (screenshot, scroll, click)
- `mcp__claude-in-chrome__read_page`
- `mcp__claude-in-chrome__find`
- `mcp__claude-in-chrome__get_page_text`

For reply drafts, use the draft pipeline (evaluates → registers in state → sends to Telegram):
```python
python3 -c "
import sys; sys.path.insert(0, '.datacore/modules/comms/lib')
from draft_pipeline import process_draft_pipeline
result = process_draft_pipeline(
    draft_reply='The actual reply text here',
    target_author='@author',
    target_content='What the target tweet said',
    target_url='https://x.com/author/status/tweet_id',
    target_tweet_id='tweet_id',
    source='engine',  # autonomous: auto-posts if guardrails pass, auto-rejects if not
)
print(f'Result: {result[\"action\"]} (draft {result[\"draft_id\"]})')
"
```

This ensures:
1. Draft is evaluated by 4 persona evaluators (voice, hemingway, orwell, critic)
2. Drafts below 60% consensus are auto-rejected (never reach Telegram)
3. Draft is registered in engagement state (Telegram callbacks work)
4. Evaluation scores are sent as follow-up message in Telegram

## Module

comms
