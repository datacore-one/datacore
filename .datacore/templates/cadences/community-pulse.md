---
cadence: community-pulse
role: ceo
frequency: weekly
duration: 20min
tools: [Bash, Read, datacore.search]
---

## Objective

Assess community health and growth trends — track engagement metrics, contributor activity, and audience growth across venture touchpoints.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the list of GitHub repos (`github.repos`), the GitHub org name, and any social/community channels configured.

2. **GitHub community metrics**: For each repo, gather key indicators:
   ```bash
   gh api repos/{org}/{repo} --jq '{stars: .stargazers_count, forks: .forks_count, open_issues: .open_issues_count, watchers: .subscribers_count}'
   ```

3. **Contributor activity**: Check unique contributors in the past 30 days:
   ```bash
   gh api repos/{org}/{repo}/stats/contributors --jq '[.[] | select(.weeks[-4:] | map(.c) | add > 0)] | length'
   ```
   Also check for new first-time contributors:
   ```bash
   gh api repos/{org}/{repo}/stats/contributors --jq '[.[] | select(.total == (.weeks[-4:] | map(.c) | add))] | length'
   ```

4. **Issue response time**: Sample recent issues to gauge responsiveness:
   ```bash
   gh issue list --repo {org}/{repo} --state closed --json number,createdAt,closedAt --limit 10
   ```
   Calculate average time-to-close for recently closed issues.

5. **PR merge velocity**: Check how quickly PRs are being merged:
   ```bash
   gh pr list --repo {org}/{repo} --state merged --json number,createdAt,mergedAt --limit 10
   ```
   Calculate average time-to-merge.

6. **Compare to previous pulse**: Search journal for the previous community-pulse entry:
   - Call `datacore.search` with "community pulse {venture_name}"
   - Compare current metrics against previous values
   - Note trends: growing, stable, or declining

7. **Identify community signals**: Look for qualitative signals:
   - Are issues being opened by new users (community adoption)?
   - Are there feature requests indicating product-market fit?
   - Are contributors returning (community stickiness)?
   - Any negative signals (complaints, abandonment patterns)?

8. **Log community pulse**: Record all metrics and trends. Capture via `datacore.capture` as a journal entry with structured metrics for future comparison.

## Output

- Community metrics snapshot: stars, forks, contributors, issue response time, PR velocity
- Week-over-week trends for each metric
- Qualitative signals noted (adoption patterns, sentiment)
- Journal entry with structured data for trend tracking

## Success Criteria

- All repos have current metrics captured
- Trends are compared against at least one previous data point
- Declining metrics are flagged with potential causes
- Community health is quantified, not just impressionistic
