# Module CLAUDE.md Template

Standard template for all Datacore module CLAUDE.md files.

## Template

```markdown
---
summary: One-line description for root CLAUDE.md
triggers: ["trigger phrase 1", "trigger phrase 2"]
context: on_match
---

# [Module] Module

## Purpose
What this module does and why. 2-3 sentences.

## Quick Start
> Say "[example trigger]" to [what happens].
> Or use `/command` for [workflow].

## How It Works

### [Operation/Workflow 1]
Brief description. `agent-name` or `/command` does X.
Pipeline (if applicable): step1 → step2 → step3

### [Operation/Workflow 2]
...

## Agents & Commands
| Name | Type | When to use |
|------|------|-------------|
| agent-1 | agent | ... |
| /command-1 | command | ... |

For modules with many agents: list primary agents, note "N+ agents total — see registry."

## Key Paths
| Path | Purpose |
|------|---------|
| `[space]/path/` | Where this module reads/writes |

## Setup
Required prerequisites and configuration. Skip section if none.

## Boundaries
What this module does NOT do. Prevents over-application. Skip if obvious.

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `datacore.recall` for those.*
```

## Guidelines

**What goes in this file**: Module purpose, workflows, agent/command reference, file paths, setup requirements, integration hooks. Facts that don't change between sessions.

**What goes in engrams**: User preferences for this module ("prefer X format"), corrections ("API Y returns Z, not W"), operational patterns ("always run X before Y"). Facts that evolve through use.

**Sizing**: Aim for 40-80 lines for simple modules, up to 150 for complex ones. If exceeding 150, content likely belongs in docs/ or engrams, not CLAUDE.md.

**Frontmatter**: Required. `summary` and `triggers` are extracted by context_merge.py for the root CLAUDE.md. `context` is `on_match` (default) or `always`.

**Sections**: Purpose, Quick Start, and How It Works are required. Agents & Commands, Key Paths, Setup, and Boundaries are include-if-applicable.
