# X Engagement (/today hook)

## Hook Context

This hook is called by `/today` to inject a live engagement section into the daily briefing.
It reads from `engagement-state.json`, `reply-queue.jsonl`, and `engagement-errors.jsonl` — no API calls required.

## Instructions

When generating the /today briefing, include an **X Engagement** section.

### Step 1: Run the health script

```bash
python3 .datacore/modules/comms/lib/engagement_health.py --today-hook
```

If the script fails or the file is missing, fall back to reading `.datacore/state/engagement-state.json` directly.

### Step 2: Include the output

The script outputs a markdown block with:
- **Posted today**: N (vs daily limit)
- **Escalated**: N (pending: N)
- **7d avg engagement score**: N.N (from analyzed replies)
- **Reply queue**: N items (Xh old) — if Chrome agent queue is non-empty
- **Action required**: N escalated replies in Telegram — if pending > 0
- **Errors (24h)**: N — if any errors logged

### Step 3: Flag actions

If escalated replies are pending, add to **Needs Your Decision**:
```markdown
- [ ] Review [N] escalated X replies in Telegram
```

If the reply queue has entries older than 4h AND more than 10 items:
```markdown
- [ ] Chrome agent may not be running — [N] items in reply queue ([X]h old)
```

## Fallback

If `engagement_health.py` is not available, show:
- Today's `daily_stats.posted` count from engagement-state.json
- Pending count from `pending[]` array
- Note: "Health dashboard not available — run engagement_health.py"

## Related

- Health dashboard: `python3 .datacore/modules/comms/lib/engagement_health.py`
- Full state: `.datacore/state/engagement-state.json`
- Error log: `.datacore/state/engagement-errors.jsonl`
- Reply queue: `.datacore/state/reply-queue.jsonl`
