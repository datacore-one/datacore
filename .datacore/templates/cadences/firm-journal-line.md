---
cadence: firm-journal-line
# Any role may carry it: each agent writes its own line, keyed by its name.
role: any
frequency: daily
# Deterministic: no agent turn, no model. The cadence runner starts the script
# in the shared space with DATACORE_POLICY_PRINCIPAL set to the agent.
script: .datacore/lib/firm_journal_line.py
timeout_minutes: 5
evidence:
  path: "journal/{date}.md"
  require: ["^## Agent day lines$", "^- \\*\\*[a-z0-9_-]+\\*\\* — "]
  min_bytes: 40
---

## Objective

Each agent leaves one line a day in the shared journal saying what its
scheduled work did, so the day can be read without opening commits or
scoreboards.

## Steps (what the script does; no agent session)

1. Read the cadence-run end events this agent signed today in every space's ledger.
2. Write one line under `## Agent day lines` in `journal/<date>.md`: how many runs
   ended, how many were ok, and the ones that were not, with their reason.
   No runs is said plainly.
3. A rerun replaces the agent's own line; nothing else on the page changes.

## Boundaries

Reads the ledger only; writes one line in one file. States only what the signed
record says.
