---
cadence: pipeline-review
role: bizdev
frequency: weekly
duration: 45min
tools: [Read, Write, WebSearch, WebFetch, datacore.search]
# DIP-0050: judged by this run's pipeline review, written during the run.
evidence:
  path: "1-tracks/business/pipeline-review-{date}.md"
  require: ["^# Pipeline Review", "^## Scored", "^## Decisions for the owner"]
  min_bytes: 800
---

## Objective

Keep the prospect pipeline qualified: score new and cooling prospects with the venture's
three-question test (`.datacore/roles/bizdev.md`), and hand the owner the pursue-or-drop
calls with their evidence. The role researches and scores; the owner decides.

## Steps

1. **Sync:** `git pull` in this space.
2. **Collect:** prospects added since the previous review (the newest
   `1-tracks/business/pipeline-review-*.md`), from research and literature notes and the CRM;
   plus entries with no interaction for 30 days.
3. **Score** each on Q1 (system of record), Q2 (integrator class), Q3 (knowledge-transfer ROI),
   1-5 each from public evidence, with the source for every score. Mean is the overall score;
   the decision bands are in the role file.
4. **Write** `1-tracks/business/pipeline-review-YYYY-MM-DD.md`, headed
   `# Pipeline Review — YYYY-MM-DD`, with:
   - `## Scored`: one row per prospect (Q1, Q2, Q3, overall, band, sources), or "none new".
   - `## Cooling`: entries gone quiet and what would revive them, or "none".
   - `## Decisions for the owner`: each Pursue with its intro path, and each Drop with its
     reason; "none" when there is nothing to decide.

**Boundary.** Never contact a prospect, send a message, or change a CRM entry's status: the
pursue-or-drop call is the owner's. Do not name a prospect outside this space.
