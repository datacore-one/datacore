---
cadence: bot-performance-report
role: operations
frequency: daily
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
evidence:
  path: "reports/bot-performance-report-{date}.md"
  require: ["^# Bot Performance Report", "^## "]
  min_bytes: 2000
---

## Objective

One page on how each trading bot did since the last report, and whether its service is healthy.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Bot Performance Report — YYYY-MM-DD`, with:
   - `## Headline`
   - `## Market snapshot`
   - `## Bot service status`
   - `## Per-bot performance`: P&L, trades, win rate, drawdown
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
