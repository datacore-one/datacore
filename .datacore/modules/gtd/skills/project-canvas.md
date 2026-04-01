# Project Canvas

A structured overview template for GTD projects. Used by `gtd-project-manager` and `user-analytics-generator` to create project status snapshots.

## Canvas Template

For each active project, generate:

### Project: [Name]

| Field | Value |
|-------|-------|
| Intent alignment | [Intent Graph intent(s)] |
| Status | [On Track / At Risk / Blocked / Complete] |
| Priority | [A/B/C] |
| Progress | [X/Y tasks done] |
| Next action | [Specific next step] |
| Blockers | [None / description] |
| Last activity | [Date] |

## Usage

- Called during weekly review to assess all active projects
- Called by `/today` for at-risk project alerts
- Can generate full canvas markdown for planning sessions

## Data Sources

- `gtd.project_health` tool — stuck/stale detection
- `gtd.agenda_view` — task counts per project
- `gtd.effort_aggregate` — work distribution
- `nightshift.task_metrics` — execution stats for :AI: tasks

## Output

Write to `0-personal/org/analytics/project-canvas-YYYY-MM-DD.md` or display inline.
