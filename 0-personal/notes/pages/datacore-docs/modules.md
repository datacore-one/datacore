---
title: Modules
type: reference
created: 2025-01-01
---

# Modules

Datacore modules extend the system with domain-specific workflows. They're optional - install only what you need.

## What's in a Module

A module is a git repository containing:

```
module-[name]/
├── module.yaml           # Module manifest
├── CLAUDE.md             # Module AI context
├── README.md             # Documentation
├── commands/             # Slash commands
│   └── [command].md
├── agents/               # AI agents
│   └── [agent].md
├── workflows/            # Process documentation
└── templates/            # File/folder templates
```

## Installing a Module

Clone the module into `.datacore/modules/`:

```bash
cd ~/Data
git clone https://github.com/datacore-one/module-trading .datacore/modules/trading
```

Commands and agents become available immediately.

## Available Modules

### module-trading

**Trading workflows for position management and performance tracking.**

Repository: `datacore-one/module-trading`

**Commands:**
- `/start-trading` - Pre-market routine
- `/validate-trade` - Check trade against rules
- `/log-trade` - Record execution
- `/close-trading` - End-of-day review
- `/check-position-health` - Risk metrics
- `/analyze-market-phase` - Market regime
- `/market-briefing` - Quick context
- `/weekly-trading-review` - Weekly performance
- `/monthly-performance` - Monthly analysis
- `/journal-digest` - Extract insights

**Agents:**
- `emergency-stop-trader` - Crisis intervention

**Install:**
```bash
git clone https://github.com/datacore-one/module-trading .datacore/modules/trading
```

---

### Future Modules (Planned)

| Module | Description |
|--------|-------------|
| `module-comms` | Content creation, social media scheduling |
| `module-devops` | Infrastructure, deployment workflows |
| `module-health` | Health tracking, habit monitoring |

## Creating Your Own Module

### 1. Create the Structure

```bash
mkdir -p my-module/{commands,agents,workflows,templates}
cd my-module
```

### 2. Create module.yaml

```yaml
name: my-module
version: 1.0.0
description: What this module does
author: your-name
repository: https://github.com/you/module-my-module

dependencies:
  - core  # if it requires core functionality

provides:
  commands:
    - my-command
  agents:
    - my-agent
```

### 3. Create CLAUDE.md

```markdown
# Module: My Module

Description for Claude Code.

## Commands

### /my-command
What it does and how to use it.

## Agents

### my-agent
When it's invoked and what it produces.
```

### 4. Add Commands

Create `commands/my-command.md`:

```markdown
# My Command

Description of what this command does.

## When to Use

Situations where this command is helpful.

## Process

1. Step one
2. Step two
3. Step three

## Output

What the user should expect.
```

### 5. Add Agents

Create `agents/my-agent.md`:

```markdown
# My Agent

Description of what this agent does.

## Trigger

How this agent is invoked.

## Input

What information is needed.

## Process

Step-by-step execution instructions.

## Output

What the agent produces and where.
```

### 6. Initialize Git

```bash
git init
git add -A
git commit -m "Initial module structure"
```

### 7. Share (Optional)

Push to GitHub and share with others:

```bash
git remote add origin git@github.com:you/module-my-module.git
git push -u origin main
```

## Module Best Practices

1. **Single responsibility.** One module, one domain.

2. **Clear dependencies.** List what the module requires.

3. **Good documentation.** README, CLAUDE.md, command docs.

4. **Sensible defaults.** Work out of the box.

5. **No personal data.** Modules are methodology, not content.

## Updating Modules

Pull the latest changes:

```bash
cd .datacore/modules/trading
git pull
```

## Removing Modules

Delete the module directory:

```bash
rm -rf .datacore/modules/trading
```

Commands and agents from that module will no longer be available.

## Module vs Built-in

**Built-in** (part of Datacore):
- GTD commands and agents
- Core functionality
- Always available

**Modules** (optional):
- Domain-specific workflows
- Installed separately
- Can be added/removed

## See Also

- [Commands Reference](commands.md)
- [Agents Reference](agents.md)
- GTD Workflow
