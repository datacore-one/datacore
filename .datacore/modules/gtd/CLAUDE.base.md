---
summary: "Task management with GTD methodology — capture, clarify, organize, delegate via org-mode"
triggers: ["add task", "process inbox", "weekly review", "plan my day", "what should I work on", "capture this"]
context: always
---

# GTD Module

## Purpose
Implements Getting Things Done methodology through org-mode. Single capture point (inbox.org), clarification into actionable lists (next_actions.org), delegation to AI agents via `:AI:` tags, and systematic reviews to keep the system trusted.

## Quick Start
> Say "add task: Review Q2 metrics" to capture to inbox.
> Say "process inbox" to clarify and route inbox items.
> Say "plan my day" for morning prioritization.
> Use `/today` for full daily briefing, `/tomorrow` for end-of-day close, `/wrap-up` for quick session end.

## How It Works

### Capture → Clarify → Organize
Everything enters `inbox.org`. Processing clarifies each item: is it actionable? If yes, route to `next_actions.org` under the right focus area with priority, effort, and tags. If delegatable, add `:AI:` tags for overnight execution.

### Daily Rhythm
`/today` — morning briefing (priorities, calendar, nightshift results, health).
`/continue` — resume incomplete work or find highest-impact next action.
`/wrap-up` — quick session close with learning capture and journal update.
`/tomorrow` — end-of-day: sync repos, queue AI tasks, set tomorrow's priorities.

### Weekly Review
Say "weekly review" for comprehensive system maintenance: process all inboxes, review next actions, check projects for stuckness, update someday/maybe, clear completed items.

### AI Delegation
Tag tasks with `:AI:subtag:` in `next_actions.org`. Nightshift picks them up overnight:
- `:AI:research:` → research-orchestrator
- `:AI:content:` → gtd-content-writer
- `:AI:data:` → gtd-data-analyzer
- `:AI:pm:` → gtd-project-manager
- `:AI:technical:` → CTO queue (human review required)

## Agents & Commands
| Name | Type | When to use |
|------|------|-------------|
| `gtd-inbox-processor` | agent | Clarifies and routes individual inbox entries |
| `gtd-inbox-coordinator` | agent | Orchestrates batch inbox processing |
| `/today` | command | Morning briefing with priorities |
| `/tomorrow` | command | End-of-day close and AI task queuing |
| `/wrap-up` | command | Quick session close with learning capture |
| `/continue` | command | Resume work or find next action |

## MCP Tools
| Tool | Purpose |
|------|---------|
| `gtd.add_task` | Capture to inbox with org-mode formatting |
| `gtd.inbox_count` | Count inbox items across spaces |
| `gtd.list_next_actions` | Query tasks by state, tags, focus area |
| `gtd.complete_task` | Mark task DONE by title or ID |
| `gtd.agenda_view` | Scheduled tasks within N days |
| `gtd.project_health` | Detect stuck/stale projects |

5 more tools available — use `datacore.modules.info gtd` for full list.

## Key Paths
| Path | Purpose |
|------|---------|
| `[space]/org/inbox.org` | Single capture point — process to zero |
| `[space]/org/next_actions.org` | Actionable tasks with :AI: tags |
| `[space]/org/habits.org` | Recurring scheduled habits |
| `[space]/org/ideas.org` | Ideas staging (score, evaluate, graduate) |

## Boundaries
- Does NOT replace org-mode — works through it via `org_workspace_adapter.py`
- Does NOT execute tasks — delegates to specialized agents via `:AI:` tags
- Does NOT manage calendar — calendar data comes from external sync

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `datacore.recall` for those.*
