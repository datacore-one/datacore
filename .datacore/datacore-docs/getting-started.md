# Getting Started with Datacore Development

Extend Datacore with custom agents, commands, and modules.

## Architecture Overview

```
~/Data/
├── .datacore/
│   ├── agents/        # AI agent definitions
│   ├── commands/      # Slash commands
│   ├── modules/       # Installed modules
│   ├── specs/         # System specifications
│   └── config.yaml    # Configuration
├── 0-personal/        # Personal space
└── 1-[space]/         # Additional spaces
```

## Extension Points

| Type | Location | Purpose |
|------|----------|---------|
| **Agents** | `.datacore/agents/*.md` | Autonomous task processors |
| **Commands** | `.datacore/commands/*.md` | User-triggered workflows |
| **Modules** | `.datacore/modules/*/` | Bundled agents + commands |

## Quick Example: Custom Command

Create `.datacore/commands/standup.md`:

```markdown
# Daily Standup

Generate a standup summary from recent activity.

## Instructions

1. Read today's journal from `0-personal/notes/journals/`
2. Extract:
   - What was completed yesterday
   - What's planned for today
   - Any blockers
3. Format as bullet points
4. Output to stdout
```

Run with: `/standup`

## Quick Example: Custom Agent

Create `.datacore/agents/link-checker.md`:

```markdown
# Link Checker Agent

Validates links in markdown files.

## Trigger

Tasks tagged with `:AI:links:`

## Instructions

1. Read the file specified in the task
2. Extract all URLs
3. Check each URL for validity
4. Report broken links
5. Update task with results
```

Use with:
```org
* TODO Check links in README :AI:links:
```

## Development Workflow

1. **Create** - Add `.md` file in appropriate folder
2. **Test** - Run command or tag a task
3. **Iterate** - Refine based on results
4. **Share** - Package as module for others

## Key Concepts

### Prompt Engineering

Agents and commands are prompt files. Write clear instructions:

```markdown
## Instructions

1. First, read the input file
2. Then, extract key information
3. Finally, format output as specified

## Output Format

- Use markdown
- Include timestamps
- Link to sources
```

### Context Access

Agents can read any file in `~/Data/`:

```markdown
Read the user's preferences from:
- `.datacore/config.yaml` for settings
- `0-personal/org/next_actions.org` for tasks
- `0-personal/notes/journals/` for recent activity
```

### Task Integration

Agents process tasks from `next_actions.org`:

```org
* TODO [task description] :AI:[agent-tag]:
  Additional context here
  Source: [url or file path]
  Output: [where to save results]
```

## Next Steps

- [Creating Agents](creating-agents.md) - Full agent development guide
- [Creating Commands](creating-commands.md) - Command development guide
- [Modules](modules.md) - Packaging and distribution
- [Specification](../specs/datacore-specification.md) - Full system spec
