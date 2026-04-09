# Analytics Module

Website analytics via PostHog — daily metrics across all projects.

## What It Does

Adds a **Web Analytics** section to `/today` showing visitors, pageviews, sessions, and day-over-day change for each PostHog project (Datacore, PLUR, Datafund).

## How It Works

`lib/posthog_daily.py` queries the PostHog HogQL API for each project and outputs a markdown table. Zero external dependencies — uses only `urllib`.

## Requirements

- `POSTHOG_API_KEY` in `.datacore/env/.env` (personal API key, `phx_` prefix)
- PostHog EU instance (`eu.posthog.com`)

## Projects Tracked

| Project | ID | Sites |
|---------|------|-------|
| Datacore | 156062 | datacore.one, softwareofyou.com |
| PLUR | 156064 | plur.ai |
| Datafund | 156069 | datafund.io |

To add a project, edit `PROJECTS` dict in `lib/posthog_daily.py`.

## Files

| File | Purpose |
|------|---------|
| `lib/posthog_daily.py` | Fetches metrics, outputs markdown |
| `commands/today-hook.md` | /today integration hook |
