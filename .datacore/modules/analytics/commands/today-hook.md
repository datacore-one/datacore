---
name: today-hook
description: today-hook command
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:today-hook
  tags:
    - today-hook
---

# Analytics Hook: /today Integration

## Command Context

### When to Reference Analytics Module

**Always reference when:**
- /today command is invoked (automatic hook)
- User asks about website traffic, visitors, or analytics

### Quick Reference

| Question | Answer |
|----------|--------|
| What format? | Markdown table with per-project metrics |
| What period? | Last 24 hours vs previous 24 hours |
| What projects? | Datacore, PLUR, Datafund |
| What if API fails? | Show "Analytics unavailable" and continue |

## Trigger

Called by `/today` command when analytics module is installed.

## Workflow

1. Run the daily metrics script (below).
2. Put its markdown table into the briefing as it comes, under "Web Analytics".
3. Add one sentence only if a project moved by more than 50% day over day.

## Section to Generate

### Web Analytics (PostHog)

Run the daily metrics script:

```bash
python3 .datacore/modules/analytics/lib/posthog_daily.py
```

Include the output directly in the briefing. The script outputs a markdown table with visitors, pageviews, sessions, and day-over-day change for each project.

## Conditions

- If the script fails or returns an error, show: `Analytics: unavailable (API error)`
- If all metrics are 0, note it may be due to recent project migration
- Do not block the rest of /today if analytics fails

## Tone

Data-first. Just the numbers, no commentary unless there's a notable spike or drop (>50% change).

## Your Boundaries

- Read-only: query PostHog, never change projects, events or settings.
- Report the script's numbers; never estimate or fill in a missing figure.
- Never print the PostHog key; it comes from the broker.

## Error Handling

- Script error or API failure: show `Analytics: unavailable (API error)` and continue with the rest of /today.
- A project returning no rows: show "n/a" for it, not zero.
- All projects at zero: say tracking may be broken, rather than reporting no traffic.
