---
cadence: audit-nightly-miles
role: coo
frequency: daily
# AUD-1..7 (spec .datacore/specs/cross-model-audit.md): a script with one model
# turn, not an agent session. miles audits tonight's capability of the promise list
# on its own model family (claude), read at pinned commits, with no tools.
script: .datacore/lib/audit_nightly.sh
timeout_minutes: 30
evidence:
  space: datacore
  path: "1-tracks/dev/audits/nightly/{date}/miles.yaml"
  require: ["^agent: miles$", "^model: claude$", "^commit: [0-9a-f]{40}$"]
  min_bytes: 200
---

## Objective

Audit one capability of Datacore's promises on the claude model family, so that
with the Firm's four families the whole promise list is covered within a week
and a problem two families agree on becomes a candidate eval for the owner.

## Steps (what the script does; no agent session)

1. Tonight's capability: `cross_model_audit.py rotation` (the promise files, read fresh).
2. Pin HEAD of every repository it reads; read the promises, their evals and
   the code those evals exercise at those commits, line-numbered.
3. One claude turn with the dev module's audit brief (core + data-loss and
   adversarial lenses), no tools, inside miles's daily cost cap.
4. Keep each finding whose evidence exists at the pinned commit; write
   `nightly/<date>/miles.yaml`. Nothing else is written.

The box aggregates the four files next morning (`cross_model_audit.py check`).
