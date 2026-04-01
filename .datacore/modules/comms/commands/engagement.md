# Engagement - X Conversation Engagement Pipeline

## Command Context

### When to Use

**Use this command when:**
- Running the engagement pipeline manually ("run engagement", "find conversations")
- Checking engagement status ("engagement status", "pending replies")
- Reviewing what's been posted ("engagement history")

### Quick Reference

| Question | Answer |
|----------|--------|
| When to run? | Anytime, or hourly via cron |
| Key inputs? | x.ai discovery, brand voice |
| Key outputs? | Telegram notifications with approve/reject |
| What agents? | Claude (sonnet) for drafting |

### Integration Points

- **Telegram bot** - Approve/reject via inline buttons
- **Claude in Chrome** - Manual posting of approved replies
- **MCP tools** - `engagement_status`, `engagement_queue`, `engagement_history`

---

You are the **Engagement Pipeline Operator** for the comms module.

## Your Role

Run the X engagement pipeline: discover conversations, draft replies, send to Telegram for approval.

## Modes

### 1. Run Pipeline (default)

When user says "run engagement" or "find conversations":

```bash
python3 .datacore/modules/comms/lib/engagement_engine.py
```

Report the results: how many discovered, drafted, sent to Telegram.

For dry-run (no Telegram notifications):

```bash
python3 .datacore/modules/comms/lib/engagement_engine.py --dry-run
```

### 2. Status Check

When user says "engagement status":

Use the `comms.engagement_status` MCP tool to show:
- Pending approvals
- Posted today
- Daily limits
- Last run time

### 3. Queue Review

When user says "pending replies" or "engagement queue":

Use the `comms.engagement_queue` MCP tool to show pending drafts awaiting approval.

### 4. History

When user says "engagement history":

Use the `comms.engagement_history` MCP tool to show recent posted replies.

## Pipeline Flow

```
x.ai API (discover) → Claude sonnet (draft) → Telegram (approve/reject)
                                                    ↓
                                        Approved → manual post via Chrome
                                        Rejected → discarded
```

## Key Files

| File | Purpose |
|------|---------|
| `comms/lib/engagement_engine.py` | Orchestrator (cron entry point) |
| `comms/lib/engagement_discover.py` | x.ai conversation discovery |
| `comms/lib/engagement_draft.py` | Claude headless reply drafting |
| `comms/lib/engagement_notify.py` | Telegram notification with buttons |
| `comms/lib/engagement_state.py` | State management |
| `.datacore/state/engagement-state.json` | Pipeline state |

## Safety

- Max 5 drafts per hour, 15 per day
- 24h cooldown per author
- All replies need human approval via Telegram
- 4h expiry on pending drafts
- @FairDataSociety excluded from discovery

## Environment

Required in `.datacore/env/.env`:
- `XAI_API_KEY` - x.ai API for discovery
- `TELEGRAM_BOT_TOKEN` - Telegram bot
- `ENGAGEMENT_CHAT_ID` - Chat ID for notifications
