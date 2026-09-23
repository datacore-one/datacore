---
cadence: weekly-plan
role: cos
frequency: weekly
# DIP-0050: judged by the plan page written during the run, in the owner's space.
# The fragment under ~/.datacore/cos is for the briefing; the page is what git holds.
script: .datacore/lib/cos_weekly_plan.sh
evidence:
  space: personal
  path: "notes/pages/weekly-plan-*.md"
  require: ["^# Weekly Plan — [0-9]{4}-W[0-9]{2}"]
  min_bytes: 1500
---

## Objective

Draft the coming week's plan (`/weekly-plan` in draft mode). `cos_weekly_plan.sh`
owns the run and its own checks; this template only says where its result is judged.
