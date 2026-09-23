---
cadence: briefing
role: cos
frequency: daily
# DIP-0050: Winston's morning is a script, not a prompt: cadence_run runs it and
# judges the page it writes into the owner's journal (another space, named
# without its host number). The box keeps the script at .datacore/lib.
script: .datacore/lib/cos_morning.sh
evidence:
  space: personal
  path: "notes/journals/{date}.md"
  require: ["^## Daily Briefing"]
  min_bytes: 500
---

## Objective

The owner's morning: sync, generate the briefing, write it into today's journal,
speak it, send the opener. `cos_morning.sh` owns the steps; this template only says
where its result is judged.
