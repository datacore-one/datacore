---
cadence: dependency-audit
role: cto
frequency: weekly
duration: 15min
tools: [Bash]
---

## Objective

Audit dependency health across venture repos — surface vulnerabilities, classify by severity, and create actionable tasks for remediation.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the list of GitHub repos (`github.repos`) and the GitHub org name.

2. **Check Dependabot alerts**: For each repo, fetch open vulnerability alerts:
   ```bash
   gh api repos/{org}/{repo}/dependabot/alerts --jq '[.[] | select(.state=="open")] | length'
   ```
   Get details for open alerts:
   ```bash
   gh api repos/{org}/{repo}/dependabot/alerts --jq '.[] | select(.state=="open") | {number, severity: .security_vulnerability.severity, package: .security_vulnerability.package.name, summary: .security_advisory.summary}'
   ```

3. **Check for open Dependabot PRs**: List automated update PRs:
   ```bash
   gh pr list --repo {org}/{repo} --author "app/dependabot" --state open --json number,title,createdAt
   ```

4. **Classify alerts by severity**:
   - **Critical/High**: Security vulnerabilities requiring immediate attention
   - **Medium**: Important but non-urgent updates
   - **Low**: Informational or minimal-risk updates

5. **Handle critical vulnerabilities**: For each critical or high severity alert:
   - Create an org task in `org/next_actions.org` with `:security:urgent:` tags
   - Include: package name, vulnerability summary, affected repo, remediation path
   - If a Dependabot PR already exists for the fix, reference it in the task

6. **Batch routine updates**: Group medium and low severity updates into a single org task per repo:
   - Heading: `Dependency updates for {repo} (week of {date})`
   - Body: list of packages needing updates with severity levels
   - Tag: `:maintenance:`

7. **Check for stale Dependabot PRs**: Flag any Dependabot PRs open >14 days:
   ```bash
   gh pr list --repo {org}/{repo} --author "app/dependabot" --state open --json number,title,createdAt --jq '.[] | select(.createdAt < "YYYY-MM-DDT00:00:00Z")'
   ```
   These may indicate compatibility issues requiring manual intervention.

8. **Log findings**: Record total alert count, severity breakdown, and actions taken.

## Output

- Severity-classified list of open dependency vulnerabilities per repo
- Org tasks with `:security:urgent:` for critical/high vulnerabilities
- Batched maintenance task for routine updates
- Stale Dependabot PRs flagged for manual review
- Summary: total alerts (critical/high/medium/low), new since last audit, resolved since last audit

## Success Criteria

- All critical and high severity alerts have corresponding org tasks
- Routine updates are batched into manageable tasks (not one per dependency)
- No Dependabot PR older than 14 days goes unmentioned
- Alert count trend is tracked week-over-week
