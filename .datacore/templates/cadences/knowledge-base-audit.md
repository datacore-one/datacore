---
cadence: knowledge-base-audit
role: quant_researcher
frequency: monthly
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
evidence:
  path: "1-tracks/research/knowledge-base-audit-{date}.md"
  require: ["^# Knowledge Base Audit", "^## "]
  min_bytes: 3000
---

## Objective

Audit the venture's research knowledge base: stale, contradicted or orphaned nodes, and what the previous audit asked for.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Knowledge Base Audit — YYYY-MM-DD`, with:
   - `## Executive summary`
   - `## Previous audit's actions and their resolution`
   - `## Node audit results`
   - `## Actions`
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
