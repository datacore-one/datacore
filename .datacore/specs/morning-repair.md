# Morning repair: errors are fixed before they reach the owner

Status: owner-directed 2026-09-25 ("a morning job that checks for all errors and does
self-repair; if code needs to change, a PR is waiting; resolve the errors before they
get to the user"). Decisions: sweep 02:00 UTC, re-check 03:30 UTC; code changes are
PRs, never merged by an agent (supersedes the 2026-09-22 merge right in autofix).

## Why

On 2026-09-25 four alerts reached the owner's phone, and each had a cause that was
known and fixable before he woke: an OS update restarted a running job; a join pulled
a briefing before it was published; two same-day trading scripts lacked egress
declarations; a visitor Mac still ran `/today` from stale data. The autofix pipeline
existed, but only for job contracts after three strikes: days for a daily job. Unit
failures and checklist failures paged the owner directly, and nothing swept the
fleet before the briefing.

## What runs

`morning_repair.py` on the box (Winston's host, where the checks and the briefing are).

1. **02:00 UTC `sweep`** collects every current failure as a *finding*, each with an
   id, its evidence and the command that re-checks it:
   - v2-verify checklist FAILs (the checklist is re-run, not read from an old log);
   - systemd units that failed in the last 24 h on the box;
   - red cadences from the liveness judge;
   - briefing inputs: mail triage (today's run completed with no errors and at least
     one account configured), open autofix escalations.
2. **Safe remediation first**, per finding kind, each one re-checked:
   - a failed unit whose last run failed and which has no newer success: run it once;
   - a briefing input that failed: rerun its producer once.
   Nothing else is done automatically: no edits, no restarts of long-running services.
3. **Anything still failing is a repair item for Miles** (ledger `item.create`, route
   dev, one per finding per day), carrying the evidence, the re-check command, and the
   rule below. A code change is a pull request whose title carries the item id.
4. **03:30 UTC `recheck`** re-runs every finding's check and writes
   `~/.datacore/cos/fragments/<date>/repairs.json`:
   `{repaired: [...], still_failing: [{finding, item, pr}], checked_at}`.
   Still-failing findings go to The Firm group as one message.
5. **The 04:00 briefing** reads the fragment: one line "repaired overnight: N", and a
   "needs you" list — PRs to review and failures nobody could repair. Nothing else about
   errors reaches the owner's 1:1 chat.

## Boundaries (what it must NOT do)

- Never weaken a check: the re-check is the finding's own command, fixed when the
  finding is made; a repair that edits the check does not count (as in autofix).
- Never merge: an agent opens PRs; the owner merges.
- Never rotate credentials, spend money, touch trading, or delete data.
- At most 10 repair items a night; beyond that, the rest are reported, not delegated.
- The sweep's own failure is itself a finding the next morning, and pages the group.

## DONE_WHEN

- A drill: a harmless unit made to fail on the box before 02:00 is re-run and green by
  03:30, appears under "repaired overnight", and sends nothing to the owner's 1:1.
- A drill: a finding with no safe remediation becomes a Miles item with the PR rule,
  and the 03:30 fragment lists it under still_failing with the item id.
- Two consecutive mornings where every alert the owner receives is either in the
  briefing's "needs you" list or in The Firm group, none in the 1:1.
