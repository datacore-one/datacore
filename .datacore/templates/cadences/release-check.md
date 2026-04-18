---
cadence: release-check
role: cto
frequency: weekly
duration: 20min
tools: [Bash, Read]
---

## Objective

Assess whether venture repos are ready for a new release based on accumulated changes, CI health, and release cadence.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the list of GitHub repos (`github.repos`) and the GitHub org name.

2. **Get last release**: For each repo, find the most recent release:
   ```bash
   gh release list --repo {org}/{repo} --limit 1 --json tagName,publishedAt,name
   ```
   If no releases exist, note the repo as unreleased and check total commit count instead.

3. **Count commits since last release**: Compare commits since the last release tag:
   ```bash
   git log {last_tag}..HEAD --oneline --no-merges | wc -l
   ```
   Or via the API:
   ```bash
   gh api repos/{org}/{repo}/compare/{last_tag}...HEAD --jq '.ahead_by'
   ```

4. **Classify accumulated changes**: Review the commit log since last release:
   ```bash
   gh api repos/{org}/{repo}/compare/{last_tag}...HEAD --jq '.commits[].commit.message'
   ```
   Categorize: features, bug fixes, breaking changes, docs, chores.

5. **Check CI on main**: Verify the default branch is green:
   ```bash
   gh run list --repo {org}/{repo} --branch main --limit 3 --json status,conclusion,name
   ```
   If CI is failing, note the failure and skip release proposal for that repo.

6. **Decide on release**: Propose a release if:
   - More than 5 commits accumulated since last release, OR
   - Any feature commits present, OR
   - More than 14 days since last release with any changes

7. **Draft release notes**: For repos meeting release criteria, compile release notes from commit messages:
   ```bash
   gh api repos/{org}/{repo}/compare/{last_tag}...HEAD --jq '.commits[].commit.message'
   ```
   Group by category (Features, Fixes, Maintenance). Suggest a version bump (major/minor/patch) based on change types.

8. **Create org task**: For each repo needing a release, create a task in `org/next_actions.org`:
   - Heading: `Release {repo} v{proposed_version}`
   - Body: draft release notes and CI status
   - Tag: `:release:` for human approval

## Output

- Per-repo assessment: commits since last release, change categories, CI status
- Release proposals with draft notes for repos meeting criteria
- Org tasks created for human approval of proposed releases

## Success Criteria

- Every repo with significant unreleased changes has a release proposal
- Release notes accurately categorize all changes since last tag
- No release proposed for repos with failing CI on main
- Version bump suggestion follows semver (breaking=major, feature=minor, fix=patch)
