# Feed Engagement — Chrome-Based Organic Activity

Run as a background agent on the local machine. Uses Chrome browser automation to
scroll the X "For you" feed, like relevant content, follow interesting accounts,
draft replies, and retweet exceptional content.

## Trigger

Conversational: "start feed engagement", "browse the feed", "engage on X"

## Prerequisites

- Chrome browser open with Claude extension connected
- Logged into X.com as the target account — the engagement agent detects which account is active and adapts topics accordingly

## Execution

**IMPORTANT**: Run this as a background agent using the Agent tool. The agent should
loop through feed scan cycles with 20-30 minute pauses between cycles.

### One Feed Scan Cycle

**Phase 0: Detect Active Account**
1. Call `tabs_context_mcp` to get current tabs
2. Find a tab on x.com, or create a new tab and navigate to `https://x.com/home`
3. Wait 3 seconds for feed to load
4. Take a screenshot — identify the logged-in account from the sidebar avatar/handle
5. Select the matching **Account Profile** (see below) for relevance scoring and voice
6. If the user requested a specific brand (e.g. `/engage Fairdrop`) but the active account doesn't match, STOP and report the mismatch

**Phase 1: Open Feed**
1. Take a screenshot to verify the feed loaded

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

2. **Get tweet URL and full text** — MANDATORY before drafting:
   1. Click through to the tweet (not just read from feed)
   2. Copy the full URL from the browser (e.g. `https://x.com/author/status/1234567890`)
   3. Extract the tweet ID from the URL (the number after `/status/`)
   4. Use `get_page_text` to capture the full tweet content
   5. **If you cannot get a valid URL with a numeric tweet ID, SKIP this tweet.**
      Never submit a draft with `unknown` as tweet_id or URL — it can't be posted later.

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

### Account Profiles

Select the profile matching the detected active account. Each profile defines
relevance scoring, search queries, and voice for that account's domain.

---

#### @FairDataSociety — Fairdrop / Fair Data Society

**Identity**: Privacy-first decentralized file sharing on Ethereum Swarm.

**Relevance Scoring (0-10):**

+3 (core): Privacy architecture, data sovereignty, self-sovereign, zero-knowledge,
E2E encryption, decentralized storage, fair data, data rights, digital sovereignty,
file sharing privacy, encrypted file transfer

+2 (adjacent): Surveillance, mass data collection, metadata tracking, WeTransfer/Dropbox
complaints, file sharing alternatives, GDPR, data breaches, privacy regulation

+1 (related): Ethereum ecosystem, Swarm network, public goods, open source,
cypherpunk, digital rights, Web3 builders (non-token)

-2 (noise): Token price, airdrop, WAGMI, engagement farming, political hot takes, celebrity

**Search queries**: `"file sharing" "privacy"`, `"decentralized storage" OR "data sovereignty"`,
`"WeTransfer" "alternative"`, `"encrypt before upload"`, `"data breach" "files"`,
`"ethereum swarm" OR "ethswarm"`, `"zero knowledge" "file"`

**Voice**: Builder of privacy infrastructure. First-person from Fairdrop experience.
- "We hit the same wall building Fairdrop. Encrypt-before-upload changed what we could promise."
- "Policy changes. Architecture doesn't."
- "And if the company gets acquired, that policy goes with it."

---

#### @plur_ai — PLUR (AI memory engine)

**Identity**: Persistent memory for AI agents — engrams, corrections, preferences
that survive across sessions. MCP-native, works with Claude Code, Copilot, Gemini.

**Relevance Scoring (0-10):**

+3 (core): AI memory, agent memory, persistent context, MCP servers, MCP tools,
engrams, agent learning, Claude Code, AI assistants remembering, context window limits

+2 (adjacent): Agentic AI, AI agents, tool use, function calling, RAG limitations,
prompt engineering, AI workflows, developer tools for AI, AI coding assistants,
Copilot CLI, Gemini CLI, agent orchestration

+1 (related): LLM tooling, AI developer experience, open source AI tools,
AI productivity, knowledge management for AI, second brain

-2 (noise): Token price, airdrop, WAGMI, engagement farming, political hot takes,
AI doomer/hype without substance, celebrity

**Search queries**: `"MCP server" OR "MCP tools"`, `"AI memory" OR "agent memory"`,
`"Claude Code" OR "claude code"`, `"AI agent" "context"`, `"persistent memory" "AI"`,
`"agentic AI" "tools"`, `"copilot CLI" OR "gemini CLI"`, `"RAG" "limitations"`

**Voice**: Builder of AI memory infrastructure. First-person from PLUR experience.
- "We built PLUR for exactly this — corrections that stick across sessions."
- "Context windows expire. Memory shouldn't."
- "What if your agent remembered why it made that choice last week?"

---

#### Default (unknown account)

If the active account doesn't match any profile above, use the @FairDataSociety
profile as fallback but warn the user about the unrecognized account.

---

**Minimum thresholds** (all accounts):
- Like: 5+ score
- Follow: 7+ score
- Reply draft: 8+ score, >1K followers
- Quote-RT draft: 9+ score, >50 likes

### Voice Guidelines for Replies (all accounts)

You are adding to someone's conversation, not broadcasting. Sound like a person.

**Anti-smugness rules (these get rejected by evaluators):**
- No "the step most miss:", "most people don't realize" — condescending
- No "X isn't a feature — it's the layer that..." — asserting authority
- No "the real fix is..." — implies knowing better than OP
- Don't use the same opener structure every time ("Spot on.", "Exactly.") — formulaic

**Pick ONE reply type per tweet:**
- Simple agreement: when OP's point stands alone, just amplify it (can be very short)
- Extension: add one new angle
- Question: genuine curiosity that extends the thread
- Experience: brief first-person from building the product (use account-specific voice above)

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
