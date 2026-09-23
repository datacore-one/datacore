---
cadence: service-uptime-report
role: operations
frequency: weekly
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
evidence:
  path: "reports/service-uptime-report-{date}.md"
  require: ["^# Service Uptime Report", "^## "]
  min_bytes: 2000
---

## Objective

The week's availability of the venture's infrastructure and services, from direct inspection, not from memory.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Service Uptime Report — YYYY-MM-DD`, with:
   - `## Infrastructure health`
   - `## Service status`: direct inspection, with UTC time
   - `## Availability estimate for the week`
   - `## Incidents`
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
