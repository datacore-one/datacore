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

1. **Load venture context**: Read `venture.yaml` to get the list of GitHub repos (`github.repos`) and the GitHub org name.

2. **Fetch open PRs**: For each repo, list open pull requests:
   ```bash
   gh pr list --repo {org}/{repo} --state open --json number,title,author,createdAt,isDraft,reviewDecision,statusCheckRollup --limit 30
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

6. **Flag stale PRs**: For PRs open >7 days with no recent commits or reviews:
   ```bash
   gh pr comment {number} --repo {org}/{repo} --body "This PR has been open for over 7 days. Is it still active? Please update or close if abandoned."
   ```

7. **Log summary**: Record PRs reviewed, actions taken, and any PRs needing human decision.

## Output

- All automated PRs with passing CI approved and tagged for human merge
- Human PRs reviewed with actionable feedback comments
- Stale PRs flagged with nudge comments
- Summary: total PRs reviewed, approved count, feedback given, stale count

## Success Criteria

- No automated PR with passing CI left without approval
- Every human PR has at least one review comment or approval
- Stale PRs (>7 days) are identified and authors notified
- No security issues in reviewed diffs go uncommented
- Zero merges executed by the agent — merge is human-only
