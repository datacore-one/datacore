---
cadence: audit-nightly-winston
role: cos
frequency: daily
# AUD-1..7 (spec .datacore/specs/cross-model-audit.md): a script with one model
# turn, not an agent session. winston audits tonight's capability of the promise list
# on its own model family (deepseek), read at pinned commits, with no tools.
script: .datacore/lib/audit_nightly.sh
timeout_minutes: 30
evidence:
  space: datacore
  path: "1-tracks/dev/audits/nightly/{date}/winston.yaml"
  require: ["^agent: winston$", "^model: deepseek$", "^commit: [0-9a-f]{40}$"]
  min_bytes: 200
---

## Objective

Audit one capability of Datacore's promises on the deepseek model family, so that
with the Firm's four families the whole promise list is covered within a week
and a problem two families agree on becomes a candidate eval for the owner.

## Steps (what the script does; no agent session)

1. Tonight's capability: `cross_model_audit.py rotation` (the promise files, read fresh).
2. Pin HEAD of every repository it reads; read the promises, their evals and
   the code those evals exercise at those commits, line-numbered.
3. One deepseek turn with the dev module's audit brief (core + data-loss and
   adversarial lenses), no tools, inside winston's daily cost cap.
4. Keep each finding whose evidence exists at the pinned commit; write
   `nightly/<date>/winston.yaml`. Nothing else is written.

The box aggregates the four files next morning (`cross_model_audit.py check`).
