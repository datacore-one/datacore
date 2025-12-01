# GTD Module

Getting Things Done — task management with org-mode.

## Tools (MCP)

All tools backed by `org_workspace_adapter.py` (org-workspace library). No inline regex.

| Tool | Description |
|------|-------------|
| `datacore.gtd.inbox_count` | Count items in inbox.org across spaces |
| `datacore.gtd.add_task` | Add task to inbox.org with org-mode formatting |
| `datacore.gtd.list_next_actions` | List TODO/NEXT tasks with filters |
| `datacore.gtd.complete_task` | Mark task DONE by title or ID |
| `datacore.gtd.agenda_view` | Tasks scheduled within N days |
| `datacore.gtd.deadline_warnings` | Tasks with deadlines within N days |
| `datacore.gtd.archive_tasks` | Archive terminal-state tasks older than N days |
| `datacore.gtd.write_clock_entry` | Write CLOCK entry to a task's LOGBOOK |
| `datacore.gtd.duplicate_check` | Find near-duplicate task titles |
| `datacore.gtd.project_health` | Detect stuck/empty/stale projects |
| `datacore.gtd.effort_aggregate` | Aggregate effort estimates by focus area |

## Key Locations

- `[space]/org/inbox.org` — Capture point (process to zero)
- `[space]/org/next_actions.org` — Actionable tasks with :AI: tags
- `[space]/org/habits.org` — Recurring scheduled habits
- `[space]/org/ideas.org` — Ideas staging area (score, evaluate, graduate)

## AI Task Tags

- `:AI:` — General AI task
- `:AI:research:` — Research processor
- `:AI:content:` — Content writer
- `:AI:data:` — Data analyzer
- `:AI:pm:` — Project manager
- `:AI:technical:` — CTO queue (human review)

## Org-mode Format

- TODO states: `TODO`, `NEXT`, `WAITING`, `DONE`
- Tags: `:tag1:tag2:`
- Timestamps: `<2026-02-20 Fri>`
- Properties: `:PROPERTIES:` ... `:END:`
