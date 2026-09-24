---
cadence: geo-sov-scan
role: cio
frequency: weekly
timeout_minutes: 120   # a multi-model scan; the 30-min default killed it on 2026-09-24
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

## Steps

1. **Sync:** `git pull` in plur-space.
2. **Scan:** from `1-tracks/geo/`, run `python3 geo_visibility.py --mode grounded`
   (OpenRouter; the key comes from the credential broker, never a file you search for).
   It writes `results/summary-<timestamp>Z.md`.
3. **Read it:** compare Share of Voice, own-domain citations and the co-occurrence list with
   the previous summary. If the scan fails, say why in your reply and stop; do not write a
   summary by hand.
4. **Push:** commit the new summary; `git push`. Do not log the run anywhere: the runner records it.
