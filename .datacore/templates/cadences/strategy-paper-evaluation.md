---
cadence: strategy-paper-evaluation
role: quant_researcher
frequency: daily
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
evidence:
  path: "1-tracks/research/strategy-paper-evaluation-{date}*.md"
  require: ["^# Strategy Paper Evaluation", "^## "]
  min_bytes: 3000
---

## Objective

Evaluate each paper-traded strategy against its hypothesis since the previous evaluation.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Strategy Paper Evaluation — YYYY-MM-DD`, with:
   - `## Summary`
   - `## Arc since the previous cycle`
   - `## One section per strategy`: trades, metrics, verdict so far
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
