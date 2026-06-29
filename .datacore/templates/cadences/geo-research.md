---
cadence: geo-research
role: cio
frequency: daily
duration: 30min
tools: [Read, Write, WebSearch, WebFetch, plur_recall_hybrid, datacore.search]
---

## Objective

Convert the top GEO content-backlog gap-queries into high-authority research drafts
that earn LLM citations. Quality over volume — at most 2 drafts per run.

## Steps

1. **Sync:** `git -C <plur-space> pull` so the backlog + drafts are current.
2. **Read the backlog:** load the newest `1-tracks/geo/results/summary-*.md` and take the
   `## Content backlog` prompts (helper: `1-tracks/geo/geo_backlog.py` —
   `latest_summary()` + `parse_content_backlog()`). Pick the top 1-2 not already covered in
   `1-tracks/geo/drafts/`.
3. **Research each pick:** WebSearch + WebFetch for current, citable evidence;
   `plur_recall_hybrid` for PLUR's positioning. Apply positioning rules: lead with memory +
   open-standard; mute on-chain/USDT; open buyer content with a pain framework, not a mechanism.
4. **Draft:** write `1-tracks/geo/drafts/<slug>-YYYY-MM-DD.md` — the first paragraph must stand
   alone (LLMs lift it verbatim); cite sources; no hype words.
5. **Hand off:** create a Data review task in `org/next_actions.org` tagged `:AI:geo:` with
   `:ASSIGNEE: data`, properties {QUERY, DRAFT_PATH, SURFACE (suggested), STAGE: review}, and a
   BOOTSTRAP telling Data to review against the rubric and route (owned vs third-party).
6. **Push:** commit the draft + the Data task; `git push`. Log the run to `cadence-log.yaml`.
