---
cadence: absorb-new-papers
role: quant_researcher
frequency: weekly
# DIP-0050: judged by this run's report, in the shape the duty has used since
# May (see the previous reports at the same path).
evidence:
  path: "1-tracks/research/absorb-new-papers-{date}.md"
  require: ["^# Absorb-New-Papers", "^## "]
  min_bytes: 2000
---

## Objective

Find and absorb the week's new papers relevant to the live strategies, with a verdict on each.

## Steps

1. **Sync:** `git pull` in this space.
2. **Read the previous report** of this cadence (the newest file matching its path, or
   under `reports/` for older ones) and pick up where it ended.
3. **Gather live data** from the sources the previous report names. Every number carries
   the UTC time it was read; say "unavailable" rather than carry an old value forward.
4. **Write the report**, headed `# Absorb-New-Papers — YYYY-MM-DD`, with:
   - `## Review of the previous cycle`
   - `## Papers found`: with links
   - `## Verdict per paper`: absorb, watch, discard) and wh
5. **Boundary.** Read-only on money: never place, modify or cancel an order, move funds, restart a bot or change its configuration. A finding that needs one of those becomes a task for the owner in `org/next_actions.org`, named in the report.
