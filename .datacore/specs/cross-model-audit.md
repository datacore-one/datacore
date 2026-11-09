# Nightly cross-model audits

Status: owner-directed 2026-09-26 ("the firm uses 4 different models, we can
leverage this in testing. We should. These are perfect nightly tasks for
agents."). Promises AUD-1..AUD-7 in
`2-datacore/1-tracks/dev/datacore-upgrade/promises/part2-agents-work-messages.yaml`
define done; evals are written from them first.

## Why

One model reviewing its own work shares its own blind spots; only an independent
check catches them. The Firm already runs four agents on four model families:

| Agent | Model | Access |
|---|---|---|
| Miles | Claude | subscription |
| Winston | DeepSeek | OpenRouter |
| Tris | GLM | OpenRouter |
| Data | GPT | OpenAI |

A finding two different families agree on is far more likely real than one
model's opinion, and a defect only one family sees is a measurable blind spot.

## What runs

1. **Nightly slice audit (each agent, its own scheduler, DIP-0050 cadence).**
   - The owner-reviewed promise list, capability by capability, is the
     rotation. Each night each agent gets a different capability, so the whole
     list is covered in about four nights.
   - Each agent reads the code behind its capability's promises at a pinned
     commit and uses the dev module's audit brief (`briefs/audit/core.md` plus
     the lens the audit-method evaluation settles on).
   - It writes findings in one schema: promise id, claim, evidence (file:line at
     the pinned commit), severity and a suggested seeded failure. They go to
     `2-datacore/1-tracks/dev/audits/nightly/<date>/<agent>.yaml`.
   - The run is a script cadence with one model turn per slice. Read-only
     tools only.
   - **Once a week per agent (AUD-1, owner 2026-09-29).** Each agent audits on
     its own night only: four audit nights, three quiet nights (the install's
     job list names the weekdays; Sunday stays calibration). The morning check
     expects only the agents whose night it was; a quiet night says nothing.
   - **Findings become GitHub issues** (`nightly --issues`,
     `file_issues()`): after the findings file is validated and committed, each
     finding is filed, under the agent's own GitHub account (refused under any
     other), as an issue labelled `audit-finding`, assigned to the principal
     whose role is "chief of operations", naming agent, model family, pinned
     commit, promise and evidence. An open issue with the same promise and
     path:line is reused, never duplicated. Repo: `cross_model_audit.issues_repo`
     in principals.yaml, else the audited checkout's origin; a finding in a
     pinned nested repository (a module) is filed there. The record is
     `<agent>.issues.yaml` beside the findings file. The chief of operations'
     GitHub triage (github module) captures each open one as a task for him.
2. **Aggregation (a script, no model).**
   - Clusters findings across the four files by promise id and evidence
     location.
   - Two or more families agree: **confirmed**. It becomes a red candidate
     eval row in the upgrade's index, for owner review, and appears once in
     the briefing's "needs you".
   - One family only: **unconfirmed**. Kept, and re-offered to another family
     in the next rotation.
3. **Weekly calibration.**
   - Every family audits the same planted-defect practice project (dev
     `evals/audit/`, ledgerlite, with answer keys).
   - The per-model recall and false-positive score is published in the
     scoreboard. This is how we know which model to trust for what, and it
     catches a model upgrade that got worse.
4. **Cross-model review of agent code (AUD-5).**
   - A PR opened by an agent (Miles's repairs, the morning repair's
     delegations) is reviewed by a different family before it reaches the
     owner.
   - The review lands as a PR comment. The author never judges its own work.

## Loop design gate (CLAUDE.md)

1. **Machine-judged exit.** A night is done when four findings files exist,
   each valid against the schema, pinned to a commit that exists, and the
   aggregation wrote its summary. Nothing else counts.
2. **The judge is not the defendant.** Aggregation is deterministic.
   Confirmation needs two different families. Calibration scores come from
   answer keys, not from the models.
3. **Boundaries.**
   - Audits only read. They never change code, tasks, the ledger or org
     files, never open or merge PRs (AUD-5 comments only), never touch
     trading or credentials, and never spend beyond a per-agent nightly cap.
   - The audit tool policy blocks writes outside the findings file.
4. **No mid-run questions.** The brief, slice and commit are fixed before the
   run. An unclear promise is reported as a finding, not asked.
5. **Stale memory.** The pinned commit and the current promise list are
   passed in on every run; no audit reads a cached copy.

Red lines:
- **Judgment stays with the owner.** Confirmed findings are candidates, not
  merged fixes.
- **A self-rewriting loop needs stricter review, not looser.** Audits never
  change their own briefs, schema or rotation.

## DONE_WHEN

- A drill: a planted defect in a throwaway copy of one capability is found by
  at least two families on its night, and appears as one confirmed candidate
  row.
- Four consecutive nights with four valid findings files each, and no write
  outside the findings folder (checked from git history).
- One weekly calibration published, with per-family recall on the practice
  project.
- A missed or failed audit night shows in The Firm group the next morning
  (AUD-6).

## Builds on

- dev module `lab`: `briefs/audit/`, `evals/audit/` fixtures and answer keys,
  plus the audit-method evaluation round 3 (iteration vs personas), which
  decides which lens the nightly brief uses.
- Cadence runner (`ventures/lib/cadence_run.py`), with per-agent schedulers and
  kill caps.
- `eval_catalog_board.py` / `promise_board.py`, for owner review of confirmed
  candidates.
