---
cadence: exchange-reconciliation
role: operations
frequency: monthly
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
evidence:
  path: "1-tracks/ops/reports/exchange-reconciliation-{date}*.md"
  require: ["^# Exchange Reconciliation", "^## "]
  min_bytes: 3000
---

## Objective

Audit each exchange balance against the venture's own ledger for the month, and account for every difference.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Exchange Reconciliation — YYYY-MM-DD`, with:
   - `## Headline`
   - `## Exchange balance audit`: per exchange, with UTC time
   - `## Reconciliation findings`
   - `## Differences left unexplained`
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
