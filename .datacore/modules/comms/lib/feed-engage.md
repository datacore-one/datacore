# Feed Engagement — Chrome-Based Organic Activity

Run as a background agent on the local machine. Uses Chrome browser automation to
scroll the X "For you" feed, like relevant content, follow interesting accounts,
draft replies, and retweet exceptional content.

## Trigger

Conversational: "start feed engagement", "browse the feed", "engage on X"

## Prerequisites

- Chrome browser open with Claude extension connected
- Logged into X.com as @FairDataSociety (or target account)

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

**Phase 5: Reply (high-value only)**
For tweets scoring 8+/10 relevance AND from accounts with >1K followers:
1. **REQUIRED: Capture the full tweet text first** — navigate to the tweet URL and use `get_page_text` to get the actual content. `target_content` must NOT be empty — this is what shows in Telegram so the reviewer can judge the reply in context.
2. Draft a reply using the voice guidelines below
3. Send draft via the pipeline command below — do NOT post directly
4. Budget: up to 5 reply drafts per cycle

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
2. Wait 3s between posts, screenshot to confirm
3. After posting all, clear the queue:
   ```bash
   ssh nightshift "echo '' > Data/.datacore/state/reply-queue.jsonl"
   ```

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
    source='chrome',
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
