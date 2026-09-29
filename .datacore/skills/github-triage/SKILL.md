---
name: github-triage
description: |
  Nightly GitHub triage for the owner's product repositories, run by Miles on
  the nightshift host. Answers "what requires attention now, what blocks the
  team or a release, which open PRs are ready or problematic, what first?",
  writes a decision-oriented report plus a decision-board data file into the
  owner's private personal space, and never modifies GitHub.
  Use for the nightly run, or when the owner asks for a GitHub triage.
---

# GitHub triage

The method is the owner's 2026-09-11 triage prompt, kept verbatim in
`reference/triage-prompt-2026-09-11.md`. **Read it in full and follow it.**
This file only adds how the nightly run is scoped, bounded and delivered.

## Inputs (given in the run prompt)

- `SCOPE` — `public` or `enterprise`. **Never both in one run.**
  - `public`: the relevant repositories under datacore-one, datafund,
    fairDataSociety, plur-ai and plur9, **excluding `plur-ai/enterprise`**.
  - `enterprise`: `plur-ai/enterprise` only. This run touches nothing public,
    and its outputs never name a customer ("the customer", "an enterprise
    deployment").
- `MODE` — `incremental` (Monday–Saturday) or `full` (Sunday).
  - `incremental`: everything waiting on the owner (review requested,
    assigned, mentioned and unanswered, his PRs with failing checks or
    requested changes, decisions only he can make), plus every issue or PR
    updated in the last 26 hours. Deep-review at most 12 PRs, those
    waiting on the owner first.
  - `full`: every open issue and PR in scope, as the reference prompt
    describes. Deep-review at most 40 PRs, ranked by the prioritisation model.
- `DATE` — the run date (YYYY-MM-DD), used in the output file names.
- `OUT` — the output folder inside `0-personal/` (`content/reports/github-triage`).
- `OWNER` — the owner's GitHub login. "Me", "my PRs" and "waiting on me" in
  the reference prompt all mean this login.

## Identity

The owner is `OWNER` from the run prompt. On the nightshift host `gh` is logged
in as the bot account (`gh api user` returns the bot), so **never** use the
authenticated account as the owner. The reference prompt's "determine my
GitHub identity" step is answered by `OWNER`. If `OWNER` is missing, stop and
write that into the report instead of guessing.

## Hard boundaries

- **Read-only on GitHub.** No merge, close, comment, review, label, assign,
  issue creation or push. The runner puts a read-only `gh` shim first on
  PATH; do not work around it (no curl or other clients against the GitHub
  API).
- **The only files you write** are the two outputs below.
- Do not modify the reference prompt or this skill.
- Stay within the deep-review cap; say in the report what you did not reach.

## Outputs

Write both into `0-personal/<OUT>/`, i.e. `0-personal/content/reports/github-triage/` (a space's reports live in its `content/reports/`, per the GTD DIP):

1. `<DATE>-<SCOPE>.md`: the report, with the reference prompt's FINAL
   OUTPUT sections 1–13, concise and decision-oriented. State MODE, the
   repositories covered and what was not reviewed.
2. `<DATE>-<SCOPE>.board.json`: the owner's decisions, in the decision-board
   data format (see below). Then run
   `python3 .datacore/skills/github-triage/triage_board.py validate <file>`
   and fix the file until it passes.

### Board data format

```json
{"meta": {"slug": "github-triage-<DATE>-<SCOPE>", "title": "GitHub triage — <SCOPE>",
          "h1": "GitHub triage", "eyebrow": "GITHUB · <SCOPE> · <MODE> · <DATE>",
          "lede": "<one sentence: what deserves attention>", "asOf": "<DATE HH:MM UTC>",
          "sources": ["content/reports/github-triage/<DATE>-<SCOPE>.md"],
          "path": [["Triage", "<MODE>"], ["Your calls", "<N> decisions"], ["Applied", "after you say “apply the decisions”"]]},
 "sections": [{"key": "...", "label": "...", "hint": "...", "noted": ["..."],
   "rows": [{"id": "Q1", "area": "<repo> #<n>", "title": "<the decision, plainly>",
             "context": "<1-2 sentences: why now, what it unblocks>",
             "options": [{"value": "merge", "label": "...", "consequence": "..."}, ...],
             "suggested": "<one option value>",
             "meta": ["<author · CI · review · age>", "https://github.com/<repo>/pull/<n>"]}]}],
 "prefill": {}}
```

Sections, in this order, and only the ones that have rows:
1. `first`: **Do first**, the ordered queue (at most 7 rows).
2. `waiting`: **Waiting on you**: reviews requested, assigned, decisions, your PRs.
3. `prs`: **PR verdicts** (merge / request changes / close / wait).
4. `release`: **Release blockers and minimum path**.
5. `close`: **Close candidates**: stale, duplicate or superseded.
6. `file`: **Missing issues to file**.

Rules for rows:
- One decision per row, with 2–4 options. Every option says what happens, and
  mutations happen only after the owner applies the board.
- Options typically include `merge`, `review` (owner reviews), `changes`
  (request changes), `close`, `keep`, `file`, `defer` and `task` (create an org task).
- At most 40 rows in total. Anything that needs no decision goes in that section's `noted` list.
- Links are https GitHub URLs only, in `meta`, never in titles.
- Plain language: say what a thing is, and put numbers and ids in brackets.

## After writing

Stop. The runner validates, commits and pushes the files. The owner renders the
board on his own machine (`triage_board.py render <file>`) and applies it in a
separate session.
