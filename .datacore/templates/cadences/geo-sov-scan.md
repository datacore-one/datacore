---
cadence: geo-sov-scan
role: cio
frequency: weekly
timeout_minutes: 140   # ~250 queries, ~100 min; below Hermes's 150-min cap (the sync guard checks)
# A script, not an agent turn: 250 queries outlast Hermes's per-turn iteration limit
# (blocked at 165/250 on 2026-09-25). The runner is in the space; the wrapper commits the summary.
script: 1-tracks/geo/run_sov_scan.sh
duration: 30min
tools: [Read, Write, Bash]
# DIP-0050: judged by a new scan summary written during the run.
evidence:
  path: "1-tracks/geo/results/summary-*.md"
  require: ["^# GEO visibility", "^## Per-model mention rate", "^## Content backlog"]
  min_bytes: 1000
---

## Objective

Measure what the major LLMs say about PLUR, and who they name instead, once a week.
The scan's content backlog is the input to geo-research.

## Steps (what the runner does; no agent is asked)

1. **Sync:** `git pull` in plur-space.
2. **Scan:** from `1-tracks/geo/`, run `python3 geo_visibility.py --mode grounded`
   (OpenRouter; the key comes from the credential broker, never a file you search for).
   It writes `results/summary-<timestamp>Z.md`.
3. **Read it:** compare Share of Voice, own-domain citations and the co-occurrence list with
   the previous summary. If the scan fails, say why in your reply and stop; do not write a
   summary by hand.
4. **Push:** commit the new summary; `git push`. Do not log the run anywhere: the runner records it.
