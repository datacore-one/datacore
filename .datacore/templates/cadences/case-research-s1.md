---
cadence: case-research-s1
role: research
frequency: weekly
# The Practice's research cell, stream S1: literature, preprints and search engines (Europe PMC, OpenAlex, Semantic Scholar, Exa, Google Scholar) for the queries in the
# venture's streams file, screened by Gemini Flash when the broker has its key.
# Deterministic plumbing: no agent turn. Owner decision 2026-10-15.
script: 1-tracks/case-research/bin/s1.sh
timeout_minutes: 60
evidence:
  path: "1-tracks/case-research/reports/S1/{date}.md"
  require: ["^# S1"]
  min_bytes: 500
---

## Objective

List what is new for stream S1 since the last run, and only that, with the rows
the screen judged off topic left out of the digest.

## Steps (what the script does; no agent session)

1. Read the identity-free streams file `1-tracks/case-research/config/research-streams.yaml`.
2. Query the stream's sources, merge into `1-tracks/case-research/research/evidence/S1.yaml`,
   check new DOIs for retractions, screen new articles against the stream's questions.
3. Write the digest `1-tracks/case-research/reports/S1/<date>.md`: new rows, the request list, notes.

## Boundaries

Reads only the streams file and public sources. Personal records never enter
this space, this template or the run.
