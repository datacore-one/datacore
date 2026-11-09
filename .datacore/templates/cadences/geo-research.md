---
cadence: geo-research
# DIP-0050: how cadence_run judges a run -- a draft written during the run,
# substantial, with a title and cited sources. Refreshing an older draft counts.
evidence:
  path: "1-tracks/geo/drafts/*.md"
  require: ["^# ", "(https?://|\\b[a-z0-9-]+\\.(com|ai|io|org|dev|net|co)/)"]   # a cited source, URL or bare domain/path
  min_bytes: 1500
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
6. **Push:** commit the draft + the Data task; `git push`. Do not log the run anywhere: the runner records it.

## DELIVERY FORMAT (IMPORTANT)
Your final response is delivered to Gregor on Telegram. Format it as a clean executive summary:

- Title: "GEO Research — {Human-friendly date}" (e.g., "Wednesday, September 24, 2026")
- One-line TL;DR: what topics you researched and drafted today
- 1-2 bullet points per draft: the gap-query it answers and the key finding
- One-line "What's next" note (what Data should review, or what's queued for tomorrow)

Do NOT include: commit hashes, file paths, verification table outputs, script names, EventLog seq numbers, or technical implementation details. Gregor wants to know what was learned and what needs review — not how the sausage was made. Keep it under 150 words. If no new drafts were produced (backlog exhausted, all covered), respond with [SILENT].
