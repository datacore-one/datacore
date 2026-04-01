# X Engagement Architecture — FDS 100+/day

## Overview

FDS's autonomous X engagement system posts 85+ principled replies/day to build voice in privacy, data sovereignty, and fair data economy conversations. Infrastructure, not campaign — runs continuously, improves from data, surfaces to human only for exceptions.

## API Budget (Basic tier, $100/month)

| Action | Daily | Monthly | API cost |
|--------|-------|---------|----------|
| Autonomous replies | 85 | 2,550 | write |
| Thread tweets | 7 | 210 | write |
| Original posts | 3 | 90 | write |
| **Total writes** | **95** | **2,850** | within 3,000 cap |
| Auto-likes (newsfeed) | 100 | — | no API (Chrome) |
| Follows | 10/session | — | no API (Chrome) |

Buffer: 150 writes/month reserved for errors, retries, and original posts.

**Upgrade path**: Pro ($5K/month) if 85/day proves high quality + ROI measurable (follower growth, inbound). Raises limit to 250/day.

## Key Constraint

**Nightshift server has no browser.** API-only:
- `x_poster.py` (OAuth 1.0a): replies, threads, original tweets ✓
- Chrome (local only): feed browsing, likes, follows — daytime/manual only

## Pipeline (every 30min, 08:00–21:00 UTC)

```
engagement_discover.py  →  engagement_engine.py  →  x_poster.py
     (18 queries)              (evaluate, tier)         (post)
                                     ↓
                         principles_evaluator.py (hard gate)
                         draft_evaluator.py (4 evaluators)
                                     ↓
                         Tier decision:
                           auto-post (consensus ≥ 0.65, followers < 50K)
                           escalate (Telegram, max 5/day)
                           reject (principles < 0.70 or consensus < 0.50)
```

## Discovery (engagement_discover.py)

18 queries per 30min cycle across 6 topic groups:

| Group | Queries | Topics |
|-------|---------|--------|
| privacy_arch | 4 | ZK proofs, E2E, self-sovereign, decentralized storage |
| surveillance | 4 | surveillance capitalism, data broker, GDPR, metadata |
| fair_data | 4 | data portability, interoperability, data commons, economics |
| ai_data | 3 | AI training rights, agents + privacy, federated learning |
| fds_mentions | 1 | @FairDataSociety, #FairData, Fairdrop (warm targets, +2 relevance) |
| infrastructure | 2 | WeTransfer complaints, self-hosted alternatives |

**Dedup**: 7-day window. Same tweet won't be re-drafted within a week.

**X search API**: 10 req/15min → 20 calls/30min cycle → 18 queries + 2 buffer.

## Evaluators

5-stage evaluation, executed in order:

### Stage 0: Principles Gate (hard gate)
`principles_evaluator.py` — fast (Haiku model)

Score < 0.70 = **hard reject**, never overridden by consensus.

| Score | Meaning |
|-------|---------|
| 1.0 | Actively supports a principle |
| 0.8 | Strongly aligned |
| 0.7 | Neutral — acceptable (most replies will land here) |
| 0.5 | Implicitly endorses centralization or compliance theater |
| 0.0 | Directly contradicts principles |

Automatic fails: praising Google Privacy Sandbox, GDPR compliance as solution, VC data monetization, centralized AI training on user data.

### Stages 1-4: Quality Evaluators
`draft_evaluator.py` — Sonnet model

| Evaluator | What it checks |
|-----------|---------------|
| voice | Additive without smugness? Varied structure? |
| hemingway | Brevity, punch, every word earns its place |
| orwell | Clarity, no pretentiousness, honest not performative |
| naturalness | Human vs AI-generated patterns |

Consensus = mean of 4 scores.

## Escalation Tiers

| Condition | Action |
|-----------|--------|
| consensus ≥ 0.65 AND followers < 50K | Auto-post immediately |
| followers ≥ 50K | Escalate to Telegram |
| 0.50 ≤ consensus < 0.65 | Escalate to Telegram |
| consensus < 0.50 | Auto-reject |
| principles < 0.70 | Auto-reject (hard gate) |

**Escalation cap**: 5/day max. Beyond cap, borderline drafts are auto-rejected.

## Content Diversity Quotas

Prevents formulaic output. Tracked in `engagement-state.json`.

| Topic | Daily target | Penalty if over |
|-------|-------------|-----------------|
| privacy_arch | 25 | -3 relevance score |
| surveillance | 20 | -3 |
| fair_data | 20 | -3 |
| regulatory | 15 | -3 |
| ai_data | 15 | -3 |
| infrastructure | 5 | -3 |

Reply type rotation: if one type > 40% of today's posts, -2 penalty for more of that type.
Reply types: agreement, extension, question, experience.

## Daily Thread (today_thread.py)

"Today in Privacy" — curated 5-7 tweet thread.

**Schedule**: Draft at 05:30 UTC → Telegram approval → auto-post at 09:00 UTC if no response.

**Sources**:
1. `newsfeed-top.jsonl` — timeline tweets scored ≥ 4 (last 24h)
2. Nightshift research outputs with privacy tag
3. High-score discovery items

**Telegram callbacks**: `thread:approve:{id}`, `thread:edit:{id}`, `thread:skip:{id}`

**Resilient posting**: saves `posted_tweet_ids` before each tweet — resumes if interrupted.

## Morning Digest (morning_digest.py)

Daily 07:30 UTC Telegram summary:
- Last 24h: auto-posted / escalated / rejected counts
- Top performer (most likes + impressions)
- Topic mix percentage
- Up to 5 escalated replies waiting for approval

**Kill switch**: `.datacore/state/campaign-kill-switch` → sends digest only, no posting.

**/today integration**: `morning_digest.py --today-hook` returns markdown section.

## Weekly Learner (engagement_learner.py)

Sunday 06:00 UTC. Activates auto-updates when ≥ 500 replies analyzed (~1 week at scale).

| Analysis | Auto-apply | When |
|----------|-----------|------|
| Preferred reply types | Yes | ≥ 500 data points |
| Topic quota weights | Yes | ≥ 500, performance data |
| Blacklist additions | Always | ≥ 3 replies, 0 engagement |
| Consensus threshold | Never | Recommendation only |
| Account tier filter | Never | Recommendation only |

Output: weekly report in `.datacore/state/engagement-reports/learner-YYYY-MM-DD.md`
+ journal entry in `0-personal/notes/journals/YYYY-MM-DD.md`

## Systemd Timers

| Timer | Schedule (UTC) | Purpose |
|-------|---------------|---------|
| `engagement-autonomous.timer` | Every 30min, 08:00–21:00 | Main engine |
| `engagement-analyzer.timer` | Every hour at :15 | Fetch 24h metrics |
| `today-in-privacy.timer` | 05:30 daily | Thread curation |
| `engagement-digest.timer` | 07:30 daily | Morning digest |
| `engagement-learner.timer` | Sunday 06:00 | Weekly analysis |

## Key Files

| File | Purpose |
|------|---------|
| `comms/lib/engagement_engine.py` | Main orchestrator — discover, draft, tier, post |
| `comms/lib/engagement_discover.py` | 18-query discovery, 7-day dedup |
| `comms/lib/principles_evaluator.py` | Hard gate against FDS principles |
| `comms/lib/draft_evaluator.py` | 4-evaluator quality panel |
| `comms/lib/morning_digest.py` | Daily 07:30 UTC digest |
| `comms/lib/today_thread.py` | "Today in Privacy" thread |
| `comms/lib/engagement_learner.py` | Sunday weekly self-improvement |
| `comms/lib/newsfeed_monitor.py` | Timeline scoring + persistence |
| `telegram/handlers/engagement.py` | Telegram approve/reject/edit callbacks |
| `.datacore/state/engagement-state.json` | All state: seen, pending, posted, quotas |
| `.datacore/state/newsfeed-top.jsonl` | 48h rolling feed of scored tweets |

## Operational Notes

### Kill switch
```bash
echo "Manual pause: campaign review" > ~/.datacore/state/campaign-kill-switch
rm ~/.datacore/state/campaign-kill-switch  # Resume
```

### Transition checklist (before enabling 85/day)
1. `python3 engagement_discover.py --dry-run` → verify 400+ candidates/day
2. `python3 principles_evaluator.py --test` → run against 10 samples
3. `python3 engagement_engine.py --autonomous --dry-run --cycles 1` → confirm pipeline
4. `python3 morning_digest.py --dry-run` → verify message format
5. `python3 today_thread.py --dry-run` → verify thread draft sources

### Upgrade to Pro
When Basic tier is consistently saturated (85 replies/day) and weekly learner shows ≥ 30% engagement rate, present business case. Pro at $5K/month requires demonstrable ROI from:
- Follower growth acceleration
- Inbound project leads
- Ecosystem positioning (citations, mentions, DMs)
