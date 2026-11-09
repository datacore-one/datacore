---
cadence: deep-literature-review
role: quant_researcher
frequency: monthly
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
# Under 1-tracks/: the space pre-commit hook refuses a root `reports/` folder
# (DIP-0015), so a report written there never left the host (2026-10-19..23).
evidence:
  path: "1-tracks/research/reports/deep-literature-review-{date}.md"
  require: ["^# Deep Literature Review", "^## "]
  min_bytes: 5000
---

## Objective

A monthly deep read on the questions the strategies currently raise, with sources.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `1-tracks/trading/reports/` or `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Deep Literature Review — YYYY-MM-DD`, with:
   - `## Executive summary`
   - `## One section per focus area, each with sources`
   - `## What changes in the research plan`
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
