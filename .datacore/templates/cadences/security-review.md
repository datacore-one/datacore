---
cadence: security-review
role: cto
frequency: weekly
duration: 20min
tools: [Bash, Read, plur_recall_hybrid]
---

## Objective

Weekly security posture assessment — verify secrets, review access changes, audit CI/CD, and ensure no unaddressed vulnerabilities across venture repos.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the list of GitHub repos (`github.repos`) and the GitHub org name.

2. **Recall security baseline**: Call `plur_recall_hybrid` with domain "security" and the venture name to retrieve known security patterns, past findings, and expected configurations.

3. **Check GitHub security advisories**: For each repo, review security advisories:
   ```bash
   gh api repos/{org}/{repo}/security-advisories --jq '.[] | {ghsa_id, severity, summary, state}'
   ```

4. **Verify expected secrets**: Confirm required secrets are configured (not their values — just their presence):
   ```bash
   gh secret list --repo {org}/{repo} --json name
   ```
   Compare against expected secrets list from venture.yaml or previous engrams. Flag any missing secrets.

5. **Review access changes**: Check for recent collaborator or team membership changes:
   ```bash
   gh api repos/{org}/{repo}/collaborators --jq '.[].login'
   ```
   Compare against known team members. Flag any new or unexpected collaborators.

6. **Audit deploy keys**: Check for deploy keys and verify they are expected:
   ```bash
   gh api repos/{org}/{repo}/keys --jq '.[] | {id, title, read_only, created_at}'
   ```
   Flag any new keys added since last review.

7. **Review CI/CD modifications**: Check for recent changes to workflow files:
   ```bash
   gh api repos/{org}/{repo}/commits --jq '[.[] | select(.commit.message | test("workflow|ci|action"; "i")) | {sha: .sha[:8], message: .commit.message, date: .commit.author.date}] | .[:5]'
   ```
   Also check for workflow files directly:
   ```bash
   gh api repos/{org}/{repo}/contents/.github/workflows --jq '.[].name'
   ```

8. **Cross-reference Dependabot alerts**: Check for unaddressed security alerts (complements the dependency-audit cadence):
   ```bash
   gh api repos/{org}/{repo}/dependabot/alerts --jq '[.[] | select(.state=="open") | select(.security_vulnerability.severity=="critical" or .security_vulnerability.severity=="high")] | length'
   ```
   Any critical/high alerts still open should be escalated.

9. **Check branch protection**: Verify main branch protection rules are in place:
   ```bash
   gh api repos/{org}/{repo}/branches/main/protection --jq '{required_reviews: .required_pull_request_reviews.required_approving_review_count, status_checks: .required_status_checks.strict, enforce_admins: .enforce_admins.enabled}' 2>/dev/null || echo "No branch protection configured"
   ```

10. **Log security posture**: Compile findings into a security status summary. Create org tasks for any issues found with `:security:` tag.

## Output

- Security posture summary per repo: advisories, secrets status, access review, CI/CD changes
- Org tasks with `:security:` tag for any issues requiring action
- Comparison against previous week's posture (from engram memory)
- Any new engrams learned about security patterns via `plur_learn`

## Success Criteria

- All repos have expected secrets present
- No unexpected collaborators or deploy keys found (or flagged if found)
- CI/CD workflow changes are reviewed and documented
- Critical/high Dependabot alerts are escalated if still unresolved
- Branch protection is verified on all main branches
