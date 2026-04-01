# GitHub Module

GitHub collaboration hub — monitors repos across your orgs, surfaces activity in /today, creates auditable GTD tasks, and enables overnight agents to respond.

## Command

```
/triage-github              # Scan all orgs, show summary, create tasks
```

## How It Works

1. **Discover repos** — derives from space git remotes, caches weekly
2. **Scan GitHub** — mentions, authored issues with comments, org-wide activity
3. **Generate summary** — your items (full detail) + org activity (compact table)
4. **Create tasks** — actionable items become org-mode tasks with :AI:github: tag
5. **Agent responds** — nightshift picks up tasks, auto-fixes simple issues or proposes solutions

## Summary Format

- **YOUR ITEMS**: full structured detail (mentions, your issues, PR reviews)
- **OTHER ACTIVITY**: compact counts per org (new issues, closed, PRs merged, comments)

## Task Audit Trail

Every GitHub action creates an org-mode task with:
- `GITHUB_URL` — link back to the issue/PR
- `GITHUB_TYPE` — issue_mention, authored_comment, pr_review
- `COMPLEXITY` — simple or complex
- `CONFIDENCE` — 0-100 agent confidence score
- `LOGBOOK` — what the agent did (PR link, comment link, proposal text)

## Agent Complexity Gate

**Simple** (auto-fix): <50 lines, >80% confidence, 1-2 files, no protected paths
**Complex** (propose): anything else — agent posts analysis + proposal as comment

## Files

| File | Purpose |
|------|---------|
| `lib/repo_discovery.py` | Discover repos from space git remotes |
| `lib/github_scanner.py` | Scan GitHub via gh CLI |
| `lib/task_creator.py` | Create org-mode tasks from scan results |
| `data/repos.json` | Cached repo list (gitignored) |
| `data/scan_cache.json` | Cached scan results (gitignored) |

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| username | plur9 | GitHub handle to monitor |
| scan_hours | 24 | How far back to scan |
| auto_task_create | true | Create GTD tasks automatically |
| confidence_threshold | 80 | Min confidence for auto-fix |
| max_change_lines | 50 | Max lines for simple classification |
| max_auto_prs | 5 | Circuit breaker per nightshift run |
