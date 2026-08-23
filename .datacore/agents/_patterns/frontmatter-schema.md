# Agent Frontmatter Schema

**Datacore Agent Configuration Standard**

All agent markdown files in `.datacore/agents/` begin with a YAML frontmatter block
that configures runtime behavior. The claude CLI reads this block directly — it is not
documentation, it is wiring.

## Schema

```yaml
---
# REQUIRED
name: my-agent                  # Identifier — matches subagent_type in Agent() calls
                                #   and the registry key in agents.yaml.
                                #   Lowercase, hyphens only, no spaces.

description: |                  # One-line or short multi-line description.
  Used by the claude CLI's agent-discovery UI and by spawning agents to
  decide which agent handles a task. Be specific about trigger conditions.
  Flat prose preferred. Multi-line uses YAML block scalar (|).

# RECOMMENDED
model: sonnet                   # Model tier. Values:
                                #   haiku   — fast, cheap; use for sub-agents
                                #             that only read/write files
                                #   sonnet  — default for most agents
                                #   opus    — complex reasoning, rare
                                #   inherit — use whatever the parent session
                                #             uses (good for sub-agents)
                                # Can also be a full model ID (e.g.
                                # claude-sonnet-4-5). Nightshift's execute.py
                                # may override this via task :MODEL: property.

tools:                          # Tool allowlist. If omitted, agent inherits
  - Read                        # the full tool set of its session. Restricting
  - Write                       # tools is the primary security surface:
  - Edit                        # sub-agents that only read should list Read,
  - Glob                        # Glob, Grep, Bash only. Orchestrators that
  - Grep                        # spawn children need Agent. Writers need Write.
  - Bash                        #
  - WebFetch                    # Valid single-tool names (as of claude 2.1.x):
  - Agent                       #   Read, Write, Edit, Glob, Grep, Bash,
  - TaskCreate                  #   WebFetch, WebSearch, Agent, Task,
  - TaskGet                     #   TaskCreate, TaskGet, TaskList, TaskUpdate,
  - TaskList                    #   TaskOutput, TaskStop, Workflow,
  - TaskUpdate                  #   AskUserQuestion, SendUserMessage,
  - TaskOutput                  #   NotebookEdit, mcp__<server>__<tool>
  - TaskStop                    #
  - AskUserQuestion             # Agent() accepts a namespace qualifier:
                                #   Agent(plugin-name:agent-name)
                                # Workflow() accepts a workflow name:
                                #   Workflow(plugin-name:workflow-name)

# OPTIONAL
effort: medium                  # Default effort hint passed to the model.
                                #   low | medium | high | xhigh
                                # Most agents omit this and let the task or
                                # the parent session decide.

color: cyan                     # UI color for this agent in the claude TUI.
                                # Standard CSS color names or hex. Cosmetic only.
                                # Useful for distinguishing sub-agents visually.
---
```

## Field Decision Guide

| Agent type | model | tools | effort | color |
|------------|-------|-------|--------|-------|
| Heavy orchestrator (ai-task-executor, research-orchestrator) | sonnet | All | omit | omit |
| Content/data worker (gtd-content-writer, gtd-data-analyzer) | sonnet | Read, Write, Edit, Glob, Grep, Bash | omit | omit |
| Lightweight sub-agent (tag-suggester, journal-entry-writer) | haiku | Read, Write, Glob, Grep | omit | omit |
| Read-only explorer | sonnet | Read, Glob, Grep, Bash | omit | cyan |
| Security/audit agent | opus | (restricted) | xhigh | (color) |

## What the claude CLI reads

The CLI reads these frontmatter fields natively as of claude 2.1.x:

- `name` — agent identifier for `subagent_type` dispatch
- `description` — shown in agent picker UI; used by spawning agents
- `model` — overrides session model for this agent's invocations
- `tools` — restricts tool access to listed tools only (allowlist)
- `effort` — sets default effort level
- `color` — UI theming
- `initialPrompt` — command or text run at agent startup (rarely needed)

Fields **not** read by the claude CLI (they belong in `agents.yaml` instead):

- `memory` — not a CLI field; use `reads.required` in registry
- `mcpServers` — not a CLI field; MCP is session-level config
- `skills`, `triggers`, `reads`, `writes`, `references`, `spawns` — registry fields

## Relationship to agents.yaml

The frontmatter configures the **runtime** (what the model can do).
`agents.yaml` configures the **registry** (what the agent knows about,
who can call it, what hooks run, what DIPs apply).

They overlap on `name`, `description`, and `model` — keep them in sync.
The registry is the authoritative source for routing and discovery;
the frontmatter is the authoritative source for tool restrictions.

## Current state (2026-08-23)

Of 45 agent files, 15 have `tools:` restricted, 30 inherit session tools.
Agents without `tools:` run with full permissions — a wider attack surface
than necessary for sub-agents that only read or write files.

Priority for adding `tools:` restrictions:
1. Sub-agents spawned autonomously (nightshift path) — highest risk
2. Agents that only read (tag-suggester, strategic-prioritizer)
3. Heavyweight orchestrators already explicit (research-orchestrator, knowledge-extractor)

## Examples

### Read-only sub-agent (haiku)
```yaml
---
name: tag-suggester
description: AI-powered tag suggestion. Analyzes text and suggests relevant tags
  from the registry, merged with user-provided tags. Called by knowledge-extractor,
  session-learning, gtd-inbox-processor.
model: haiku
tools:
  - Read
  - Glob
  - Grep
---
```

### File-writing worker (sonnet)
```yaml
---
name: gtd-data-analyzer
description: Autonomous data processing and reporting agent that extracts data from
  journals and org files, calculates metrics, generates insights, and creates reports.
  Invoked by ai-task-executor for :AI:data: tagged tasks.
model: sonnet
tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
---
```

### Orchestrator that spawns sub-agents (sonnet)
```yaml
---
name: ai-task-executor
description: Core 24/7 autonomous task execution hub that scans next_actions.org for
  :AI: tagged tasks, routes them to specialized GTD agents, and logs outcomes.
model: sonnet
tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - Agent
  - TaskCreate
  - TaskList
  - TaskUpdate
---
```
