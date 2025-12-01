# Datacore Documentation

Developer documentation for extending and customizing Datacore.

## Getting Started

- [Getting Started](getting-started.md) - Architecture overview and extension points

## Development Guides

- [Creating Agents](creating-agents.md) - Build autonomous task processors
- [Creating Commands](creating-commands.md) - Build user-triggered workflows
- [Modules](modules.md) - Package and distribute extensions

## Reference

- [Commands Reference](commands.md) - All built-in commands
- [Agents Reference](agents.md) - All built-in agents
- [Specification](../specs/datacore-specification.md) - Full system specification

## Quick Start: Custom Command

```markdown
# .datacore/commands/hello.md

# Hello

Say hello with today's date.

## Behavior

1. Get current date
2. Output greeting with date
```

Run: `/hello`

## Quick Start: Custom Agent

```markdown
# .datacore/agents/summarizer.md

---
name: summarizer
description: Summarizes text content
model: haiku
---

# Summarizer Agent

Summarize text files.

## When You're Called

Tasks tagged with `:AI:summarize:`

## Workflow

1. Read the file specified in task
2. Generate 3-sentence summary
3. Save to same location with `.summary.md` suffix
```

Use:
```org
* TODO Summarize meeting notes :AI:summarize:
  File: notes/meeting-2024-11-20.md
```

## Extension Points

| Type | Location | Trigger |
|------|----------|---------|
| Commands | `.datacore/commands/*.md` | `/command-name` |
| Agents | `.datacore/agents/*.md` | `:AI:tag:` in tasks |
| Modules | `.datacore/modules/*/` | Bundled extensions |

## Architecture

```
~/Data/
├── .datacore/
│   ├── agents/          # Agent definitions
│   ├── commands/        # Command definitions
│   ├── modules/         # Installed modules
│   ├── specs/           # System specifications
│   └── config.yaml      # Configuration
├── 0-personal/          # Personal space
│   ├── org/             # Task management
│   └── notes/           # Knowledge base
└── 1-[space]/           # Additional spaces
```

## Resources

- [Installation Guide](../../INSTALL.md)
- [Main README](../../README.md)
- [Module Catalog](../../CATALOG.md)
