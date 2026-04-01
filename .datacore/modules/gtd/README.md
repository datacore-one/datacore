# GTD Module

Getting Things Done task management with org-mode for Datacore.

## Features

- **Inbox capture** -- single entry point, process to zero
- **Task management** -- TODO/NEXT/WAITING/DONE states with org-mode
- **AI delegation** -- tag tasks with `:AI:` for overnight execution
- **13 MCP tools** -- inbox, tasks, habits, deadlines, archiving, dedup, health, effort, trigger lists
- **Daily rhythms** -- `/today`, `/continue`, `/tomorrow`, `/wrap-up`
- **Weekly review** -- trigger list prompts for comprehensive brainstorming

## Tools

| Tool | Description |
|------|-------------|
| `inbox_count` | Count inbox items across spaces |
| `add_task` | Add task with org-mode formatting |
| `list_next_actions` | List TODO/NEXT tasks |
| `complete_task` | Mark task DONE |
| `habits_due` | Habits due today |
| `agenda_view` | Flexible task queries (state, tags, area, deadline) |
| `deadline_warnings` | Upcoming and overdue deadlines |
| `archive_tasks` | Archive old DONE tasks |
| `write_clock_entry` | CLOCK time entries |
| `duplicate_check` | Detect near-duplicate tasks |
| `project_health` | Stuck projects and stale items |
| `effort_aggregate` | Effort estimates by area and state |
| `trigger_list` | Weekly review brainstorming prompts |

## Installation

Included by default in Datacore. See [CLAUDE.base.md](CLAUDE.base.md) for full documentation.

## Specification

[DIP-0009: GTD Specification](../../dips/DIP-0009-gtd-specification.md)

## License

MIT
