---
cadence: case-research-s10
role: research
frequency: daily
# The Practice's research cell, S10: the lead desk. Reads the evidence the
# streams added since the last board against the open leads, writes one step
# per lead and a critique by a second model, and a board for the owner. Script
# cadence: the script calls the models itself; no agent turn. Owner decision 2026-10-16.
script: 1-tracks/case-research/bin/s10.sh
timeout_minutes: 60
evidence:
  path: "1-tracks/case-research/reports/S10/{date}.md"
  require: ["^# S10"]
  min_bytes: 500
---

## Objective

Each morning, connect what the streams found to the open leads, move each lead
one step or say why it could not move, and show the owner what changed.

## Steps (what the script does; no agent session)

1. Read the evidence tables, the lead files in `1-tracks/case-research/leads/` and the
   anonymised fact sheet `1-tracks/case-research/config/case-facts.yaml`.
2. Triage the rows kept since the last board against every open lead.
3. For each lead with new evidence: one dated step citing only those rows, then a critique by a
   different model, both appended to the lead file.
4. Write the board `1-tracks/case-research/reports/S10/<date>.md`; print a short summary.

## Boundaries

No treatment advice. Only the owner sets a lead to ready; no lead file is deleted.
Personal records never enter this space, this template or the run.
