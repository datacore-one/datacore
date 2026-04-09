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
