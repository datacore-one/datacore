---
cadence: audit-calibration-tris
role: cio
frequency: weekly
# AUD-4: tris's model family (glm) audits the planted-defect practice project
# (dev module lab: evals/audit/fixture2, ledgerlite) once a week; the box scores
# every family's run against the answer key and publishes calibration/<week>.yaml.
script: .datacore/lib/audit_calibration.sh
timeout_minutes: 30
evidence:
  space: datacore
  path: "1-tracks/dev/audits/calibration/runs/{date}-tris.yaml"
  require: ["^agent: tris$", "^model: glm$", "^commit: [0-9a-f]{40}$"]
  min_bytes: 200
---

## Objective

Measure how many of the practice project's planted defects the glm family finds,
and how many false alarms it raises, so the owner can see which model to trust
for what and notice when a model upgrade got worse.

## Steps (what the script does; no agent session)

1. Read the practice project at the dev module's `lab` commit (never its answer key).
2. One glm turn with the audit brief, no tools, inside tris's daily cost cap.
3. Write `calibration/runs/<date>-tris.yaml`. The score comes from the key, on the box.
