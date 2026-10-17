# Analytics Module

Website analytics via PostHog — daily metrics across all projects.

## What It Does

Adds a **Web Analytics** section to `/today` showing visitors, pageviews, sessions, and day-over-day change for each configured PostHog project.

## How It Works

`lib/posthog_daily.py` queries the PostHog HogQL API for each project and outputs a markdown table. Zero external dependencies — uses only `urllib`.

## Requirements

- `POSTHOG_API_KEY` in `.datacore/env/.env` (personal API key, `phx_` prefix)
- PostHog EU instance (`eu.posthog.com`)

## Projects Tracked

The project list is per installation: `projects.local.json` in this module
(gitignored, `{"Name": project_id}`) or `POSTHOG_PROJECTS="Name=id,Name=id"`.
Without either, the script says so and exits 1.


## Files

| File | Purpose |
|------|---------|
| `lib/posthog_daily.py` | Fetches metrics, outputs markdown |
| `commands/today-hook.md` | /today integration hook |
