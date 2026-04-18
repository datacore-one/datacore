---
cadence: github-issue-triage
role: cto
frequency: daily
duration: 15min
tools: [Bash, Read, plur_recall_hybrid]
---

## Objective

Triage all open GitHub issues across venture repos — classify, label, prioritize, and route actionable items.

## Steps

1. **Load venture context**: Read `venture.yaml` to get the list of GitHub repos (`github.repos`) and the GitHub org name.

2. **Fetch open issues**: For each repo, list open issues without triage labels:
   ```bash
   gh issue list --repo {org}/{repo} --state open --json number,title,labels,createdAt,author --limit 50
   ```
   Filter to issues missing classification labels (bug, feature, question, docs, duplicate).

3. **Read and classify each untriaged issue**: For each issue:
   ```bash
   gh issue view {number} --repo {org}/{repo} --json body,comments,labels
   ```
   Classify into one of: `bug`, `feature`, `question`, `docs`, `duplicate`.

4. **Assess priority**: Based on issue content, assign priority:
   - **critical** — production down, data loss, security vulnerability
   - **high** — broken functionality affecting users
   - **medium** — non-blocking bugs, important features
   - **low** — cosmetic, nice-to-have, minor improvements

5. **Apply labels**: Add classification and priority labels:
   ```bash
   gh issue edit {number} --repo {org}/{repo} --add-label "{classification},priority:{level}"
   ```

6. **Handle special cases**:
   - **Critical issues**: Create an org task in `org/next_actions.org` with `:urgent:` tag and link to the issue.
   - **Questions**: Draft a helpful response and post as a comment:
     ```bash
     gh issue comment {number} --repo {org}/{repo} --body "response text"
     ```
   - **Duplicates**: Link to the original issue and close:
     ```bash
     gh issue close {number} --repo {org}/{repo} --comment "Duplicate of #{original}. Closing."
     ```

7. **Check engram memory**: Call `plur_recall_hybrid` for any recurring issue patterns or known workarounds relevant to the issues found.

## Output

- All open issues triaged with classification and priority labels
- Critical issues captured as org tasks with `:urgent:` tag
- Questions answered with draft responses
- Duplicates linked and closed
- Brief summary logged: total issues triaged, breakdown by classification, any critical items flagged

## Success Criteria

- Zero untriaged issues remain across all venture repos
- Critical issues have corresponding org tasks created within the same session
- Duplicate issues are linked to originals before closing
- Labels are consistent with the classification taxonomy (bug/feature/question/docs/duplicate)
