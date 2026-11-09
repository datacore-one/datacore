---
cadence: audit-trail-append
role: operations
frequency: daily
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
evidence:
  path: "1-tracks/ops/reports/audit-trail-append-{date}*.md"
  require: ["^# Audit Trail Append", "^## "]
  min_bytes: 3000
---

## Objective

Append the day to the audit trail: every timer and hold since the previous entry, every discrepancy opened or closed, and NAV.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Audit Trail Append — YYYY-MM-DD`, with:
   - `## Arc summary`: from the previous entry's end to now
   - `## Timers and holds`
   - `## Discrepancies`: opened, closed, still open
   - `## NAV and its change`
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
