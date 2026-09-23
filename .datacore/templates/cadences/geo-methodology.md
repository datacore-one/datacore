---
cadence: geo-methodology
role: cio
frequency: daily
duration: 20min
tools: [Read, Write, plur_recall_hybrid]
# DIP-0050: judged by today's methodology note, written during the run.
evidence:
  path: "1-tracks/geo/geo-methodology-{date}.md"
  require: ["^# GEO Methodology Note", "^## "]
  min_bytes: 600
---

## Objective

Keep the GEO method honest: what the latest scan shows, what the published content
should be moving, and what to measure next. One short note a day; say plainly when
nothing changed and why.

## Steps

1. **Sync:** `git pull` in plur-space.
2. **Read the evidence:** the newest `1-tracks/geo/results/summary-*.md` (scan date, Share of
   Voice per model, own-domain citations, the content backlog), the drafts and their review
   state in `1-tracks/geo/drafts/`, and what was published (`1-tracks/geo/published/`).
3. **Write** `1-tracks/geo/geo-methodology-YYYY-MM-DD.md` (date from the date tool), headed
   `# GEO Methodology Note — YYYY-MM-DD`, with sections:
   - `## Scan coverage`: the latest scan and its age; whether a new scan is due.
   - `## What moved`: SoV or citation changes since the previous note, with numbers, or "none".
   - `## Publication funnel`: drafts waiting, their blockers and how long they have waited.
   - `## Method changes`: anything to measure differently, or "none".
4. **Learn:** record a genuinely new methodology insight with `plur_learn` (group:plur/comms);
   skip it when there is none.
5. **Push:** commit the note; `git push`. Do not log the run anywhere: the runner records it.
