# Agents Reference

All available agents in Datacore.

## Core Agents

### Task Execution

| Agent | Purpose | Trigger |
|-------|---------|---------|
| `ai-task-executor` | Routes :AI: tasks to specialized agents | 24/7 scanning |
| `gtd-content-writer` | Blog, email, docs, social content | `:AI:content:` |
| `gtd-research-processor` | URL analysis, literature notes, zettels | `:AI:research:` |
| `gtd-data-analyzer` | Metrics, reports, insights | `:AI:data:` |
| `gtd-project-manager` | Project status, blockers, escalation | `:AI:pm:` |

### Inbox Processing

| Agent | Purpose | Pattern |
|-------|---------|---------|
| `gtd-inbox-processor` | Process single inbox entry | Subagent |
| `gtd-inbox-coordinator` | Orchestrate batch inbox processing | Coordinator |

### Session Wrap-Up (Coordinator Pattern)

| Agent | Purpose | Pattern |
|-------|---------|---------|
| `journal-entry-writer` | Write journal entry for single space | Subagent |
| `journal-coordinator` | Orchestrate journals across all spaces | Coordinator |
| `session-learning` | Extract learnings for single space | Subagent |
| `session-learning-coordinator` | Orchestrate learning extraction across spaces | Coordinator |

**Coordinator Pattern:** Coordinators discover spaces dynamically via `[0-9]-*/` and spawn subagents in parallel.

### Knowledge Processing

| Agent | Purpose | Trigger |
|-------|---------|---------|
| `conversation-processor` | Process ChatGPT exports into knowledge | Manual |
| `research-link-processor` | Batch URL processing | Manual |

### System Maintenance

| Agent | Purpose | Trigger |
|-------|---------|---------|
| `context-maintainer` | Update CLAUDE.md tables | On agent/command changes |
| `module-registrar` | Register module agents/commands | On module install |
| `scaffolding-auditor` | Audit system scaffolding | Manual |
| `archiver` | Archive completed tasks | On state transition |

### Utility

| Agent | Purpose | Trigger |
|-------|---------|---------|
| `dip-preparer` | Prepare DIP documents | Manual |
| `landing-generator` | Generate landing pages | Manual |

## Agent Locations

| Location | Scope |
|----------|-------|
| `.datacore/agents/` | All spaces (symlinked to `.claude/agents/`) |
| `0-personal/.datacore/agents/` | Personal space only |
| `[N]-[space]/.datacore/agents/` | That space only |

## Creating New Agents

See [Creating Agents](creating-agents.md) for the agent file format and best practices.

## Coordinator-Subagent Pattern

For operations that need to run across multiple spaces:

```
command → coordinator → [discover spaces via [0-9]-*/]
                     → subagent (space: 0-personal)
                     → subagent (space: 1-[name])
                     → subagent (space: 2-[name])
                     → ...
```

**Key principles:**
- Coordinators discover spaces dynamically (never hardcode)
- Spawn ALL subagents in parallel (single message with multiple Task calls)
- Each subagent writes to space-specific files
- Coordinator aggregates and returns summary

**Examples:**
- `gtd-inbox-coordinator` → `gtd-inbox-processor` × N
- `journal-coordinator` → `journal-entry-writer` × N
- `session-learning-coordinator` → `session-learning` × N

## Related

- [Creating Agents](creating-agents.md)
- [DIP-0009: GTD Specification](../dips/DIP-0009-gtd-specification.md)
