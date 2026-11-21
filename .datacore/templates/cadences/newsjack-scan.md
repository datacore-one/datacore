---
cadence: newsjack-scan
role: cio
frequency: daily
duration: 45min
tools: [Read, Write, Bash, WebSearch, WebFetch, plur_recall_hybrid]
# DIP-0050: judged by a newsjack run report written during the run.
evidence:
  path: "1-tracks/comms/newsjack/monitor/runs/*/run.md"
  require: ["^# ", "(pitch_ready|big_story|watch)"]
  min_bytes: 800
---

## Objective

Find the news from the last 24 hours that PLUR has real standing to react to (agent memory, self-learning
agents, recursive self-improvement, AI data ownership, local AI, MCP, competitors), so a reaction can go out
on day 0–1, which is when it counts. Owner decision, 21 Nov 2026: daily, as Tris's job. It sits next to the
GEO jobs and does not replace them. GEO measures what answer engines say; this watches the news.

## Steps

1. **Sync:** `git -C <plur-space> pull`.
2. **Load the method:** read `~/Data/.datacore/modules/comms/vendor/newsjack/skills/newsjack-detector/SKILL.md`
   and follow it, reading the sibling skills it names from the same folder
   (`relevance-coarse-filter`, `story-origin-check`, `newsjack-triage`, `angle-generator`). Read
   `../ETHICS.md` and `../WHY-NOT-SPAM.md` there first; they override everything else.
3. **The monitor:** the PLUR profile and `brief.md` are in `1-tracks/comms/newsjack/monitor/` (in the space).
   The brief decides what may be pitched and how results are shown.
4. **Run:** the canonical pipeline over the last 24 hours with the newsjack CLI (`newsjack --version`; if it
   is missing, stop and say so: the host needs it installed, see the comms module `vendor/README.md`).
   No Medialyst key exists: use RSS, Hacker News and WebSearch, and say that coverage is reduced.
   Write the run folder under `1-tracks/comms/newsjack/monitor/runs/<UTC timestamp>/` with `run.md`.
5. **Hand off:** for each `pitch_ready` item, create a Data task in `org/next_actions.org` tagged
   `:AI:comms:` with the story, first-public time, PLUR's standing, the suggested angle, and "react by"
   (first-public + 24h). Nothing is posted; Data drafts, and the owner's gates apply.
6. **Push:** commit the run folder and tasks; `git push`. The runner records the run.

## DELIVERY FORMAT (IMPORTANT)
Your final response goes to Gregor on Telegram:

- Title: "News to react to — {Human-friendly date}"
- Up to 3 bullets: the story, why PLUR can speak to it, react-by time
- One line on big stories PLUR has no standing on (awareness only)

No file paths, commands or pipeline internals. Under 120 words. If nothing is pitch-ready and there are no
big stories, respond with [SILENT].
