---
cadence: pr-review
role: cto
frequency: daily
duration: 15min
tools: [Bash, Read]
---

## Objective

Review all open pull requests across venture repos — assess code quality, check CI, approve automations, and flag stale PRs.

## Steps

0. **Address feedback on your own PRs — FIRST, every run, regardless of age.**

   You opened these. Nobody else will close the loop. Do this before reviewing
   anyone else's work.

   ```bash
   gh search prs --author=@me --state=open --json number,title,repository --limit 60
   # then per PR:
   gh pr view {number} --repo {org}/{repo} --json reviewDecision,reviews,comments,updatedAt
   ```

   For every PR where `reviewDecision` is `CHANGES_REQUESTED`, or a review/comment
   is newer than your last commit on the branch:

   - **Read the review body.** Address it in code on the same branch and push.
     Then reply on the PR saying what changed, referencing the specific point.
   - **If the request needs a human decision** (a product call, a scope question,
     a security trade-off), reply on the PR stating the question plainly and
     escalate. Do not leave it silent.
   - **Do not** file an org task and wait for the next nightshift cycle. That is
     the round-trip this step exists to remove. Fix it now, in this cadence.
   - **Do not** self-approve, and do not merge. Humans merge.

   **Do not gate this on staleness.** Step 6 handles PRs that have gone quiet;
   this step handles feedback that has *arrived*, however recently.

   > **Why this step exists:** on 2026-08-31, 25 of 41 open PRs authored by
   > `miles-on-nightshift` needed the author to come back. Seven carried
   > `CHANGES_REQUESTED` untouched for a week, including plur#942 where a
   > reviewer had explained precisely what to change. Two in plur-space had sat
   > 109 and 50 days. Opening a PR is not finishing the task; the task is
   > finished when the PR is merged or closed.

1. **Load venture context**: Read `venture.yaml` to get the list of GitHub repos (`github.repos`) and the GitHub org name.

2. **Fetch open PRs**: For each repo, list open pull requests:
   ```bash
   gh pr list --repo {org}/{repo} --state open --json number,title,author,createdAt,isDraft,reviewDecision,statusCheckRollup,reviews,headRefName,baseRefName,mergeable --limit 30
   ```

3. **Categorize PRs**: Split into:
   - **Automated PRs** (Dependabot, renovate, bots) — fast-track review
   - **Human PRs** — full review required
   - **Draft PRs** — skip unless stale
   - **Stale PRs** (created >7 days ago, no recent activity) — flag for action

4. **Review automated PRs**: For each Dependabot/bot PR:
   ```bash
   gh pr checks {number} --repo {org}/{repo}
   ```
   If all CI checks pass, approve and tag plur9 for merge — **do not merge yourself**:
   ```bash
   gh pr review {number} --repo {org}/{repo} --approve --body "CI passing, automated update approved. @plur9 ready for merge."
   ```
   If CI fails, add a comment noting the failure and skip.

   **Policy:** Agents open and review PRs. Humans merge. This applies to **all** PRs — bot, human, and agent-authored. Do not run `gh pr merge`. Branch protection on main will reject it; this is the corresponding behavioral rule.

5. **Review human PRs**: For each human PR:
   - Read the diff:
     ```bash
     gh pr diff {number} --repo {org}/{repo}
     ```
   - Check CI status:
     ```bash
     gh pr checks {number} --repo {org}/{repo}
     ```
   - Assess: code quality, security concerns (secrets, injection, auth), test coverage, breaking changes.
   - Leave review comments on specific lines or an overall review:
     ```bash
     gh pr review {number} --repo {org}/{repo} --comment --body "Review feedback here"
     ```

6. **Route stale PRs**: For PRs open >7 days with no recent activity, branch on state — **never post a generic nag**:

   - **Author is an agent account** (`plur9`, `miles-on-nightshift`, or any bot you operate): Do NOT comment on your own PR.
     Note: step 0 should already have addressed anything actionable here. If a PR reaches this branch still carrying `CHANGES_REQUESTED`, that is a step-0 miss worth logging.
     - `CHANGES_REQUESTED`: Read the review body from the `reviews` field. If the findings are auto-addressable, file an org task naming them explicitly. If not, escalate to the human operator with a one-line summary of the specific blocker.
     - `mergeable == DIRTY`: File an org task to rebase the PR onto its base branch.
     - `statusCheckRollup` failing / CI never ran: Investigate the root cause — wrong base, missing workflow trigger. Fix or escalate with specifics. Do not comment.

   - **Author is an external human and ball is genuinely with them**: Post a specific nudge (not a template), naming the blocking review finding or CI failure. De-duplicate: if an identical comment already exists on the PR, skip.
     ```bash
     # Only when: external author, no response for >7 days, ball is with them
     gh pr comment {number} --repo {org}/{repo} --body "Reminder: [specific finding or CI failure blocking merge]. Let me know if you need help or want to close this."
     ```

   - **`reviewDecision` is null / no review yet**: Leave no comment. The PR is simply open; add a review if the diff is ready to assess (per step 5).

   - **`mergeable == UNKNOWN`**: Skip — GitHub is still computing the merge state; re-check next run.

7. **Log summary**: Record PRs reviewed, actions taken, and any PRs needing human decision.

## Output

- All automated PRs with passing CI approved and tagged for human merge
- Human PRs reviewed with actionable feedback comments
- Stale PRs flagged with nudge comments
- Summary: total PRs reviewed, approved count, feedback given, stale count

## Success Criteria

- No automated PR with passing CI left without approval
- Every human PR has at least one review comment or approval
- Stale PRs (>7 days) are routed (task filed, escalated, or specific nudge) — never a generic nag
- No generic self-addressed nag comments posted (agent-authored PRs are never nagged at themselves)
- No security issues in reviewed diffs go uncommented
- Zero merges executed by the agent — merge is human-only
