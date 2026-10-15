---
cadence: case-research-s6
role: research
frequency: daily
# The Practice's research cell, stream S6: public trial registries and drug
# approvals for the queries in the venture's streams file. Deterministic: no
# agent turn, no model. Owner decision 2026-10-15.
script: 1-tracks/case-research/bin/s6.sh
timeout_minutes: 30
evidence:
  path: "1-tracks/case-research/reports/S6/{date}.md"
  require: ["^# S6"]
  min_bytes: 500
---

## Objective

Each morning, list what changed in the registries since the last run, and only
that, so nobody has to search by hand.

## Steps (what the script does; no agent session)

1. Read the identity-free streams file `1-tracks/case-research/config/research-streams.yaml`.
2. Query ClinicalTrials.gov, the EU trial registry (CTIS) and openFDA for stream S6.
3. Merge into the evidence table `1-tracks/case-research/research/evidence/S6.yaml` and write
   the digest `1-tracks/case-research/reports/S6/<date>.md`: new rows, trial status changes,
   the request list.

## Boundaries

Reads only the streams file and public sources. Personal records never enter
this space, this template or the run.
