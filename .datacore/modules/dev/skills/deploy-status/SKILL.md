---
name: deploy-status
description: Quick CI/CD status check for a project — shows latest workflow runs without deploying
user-invocable: true
---

# Deploy Status

## Instructions

Quick status check for a project's CI/CD pipeline. Does NOT deploy — just reports current state.

Usage: `/deploy-status [project]`

Parse `$ARGUMENTS` for a project name. If none given, detect from cwd or ask.

### Workflow

1. **Find deploy.yaml** — in the project root (same resolution as `/deploy`)
2. **Query GitHub Actions** — get recent workflow runs:
   ```bash
   gh api repos/[gh_repo]/actions/runs --jq '.workflow_runs[:5] | .[] | {id, name: .name, status, conclusion, head_sha: .head_sha[:7], created_at, html_url}'
   ```
3. **Show latest run details** — if a run is in progress, show job-level status:
   ```bash
   gh api repos/[gh_repo]/actions/runs/[run_id]/jobs --jq '.jobs[] | {name: .name, status, conclusion}'
   ```
4. **Compare with local** — show if local HEAD matches the latest deployed commit:
   ```bash
   git log --oneline -1
   git log [remote]/[branch] --oneline -1
   ```

### Output Format

```
Deploy Status: [project]
Repo: [gh_repo]

Latest run: #[number] ([status])
  Commit: [sha] "[message]"
  Started: [time ago]
  Jobs:
    Unit Tests        passed
    Build             passed
    Deploy            in_progress

Local HEAD: [sha] "[message]"
  [N commits ahead of remote | up to date | behind remote]
```

If no runs found or gh CLI not authenticated, report the issue clearly.
