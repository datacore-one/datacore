# Team Onboarding Guide

Welcome to Datacore! This guide helps new team members get started safely.

## Quick Start (Day 1)

### 1. Clone the Space

```bash
# For Datafund team members
git clone git@github.com:datafund/space.git ~/Data/1-datafund

# For Datacore developers
git clone git@github.com:datacore-one/datacore-space.git ~/Data/2-datacore
```

### 2. Install Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

### 3. Start a Session

```bash
cd ~/Data
claude
```

### 4. Run Your First Commands

Safe commands to try immediately:
```
/today              # See daily briefing
/diagnostic         # Check system health
```

## Golden Rules

| DO | DON'T |
|----|-------|
| Edit `.base.md`, `.space.md`, `.local.md` | Edit `CLAUDE.md` directly |
| Add to `org/inbox.org` | Delete existing org headings |
| Create notes in `0-inbox/` | Modify `next_actions.org` structure |
| Use `./sync` for git operations | Push directly without review |
| Ask before changing agents/commands | Commit `.local.md` files |

## File Layers (Important!)

Datacore uses a layered context system:

| File | Layer | Who Sees It | Can Edit? |
|------|-------|-------------|-----------|
| `*.base.md` | PUBLIC | Everyone | Yes - PRs welcome |
| `*.space.md` | SPACE | Team only | Yes - tracked in space repo |
| `*.local.md` | PRIVATE | Only you | Yes - never committed |
| `*.md` (composed) | Generated | AI reads this | NO - auto-generated |

**Key insight**: If you see `<!-- AUTO-GENERATED -->` at the top, don't edit it!

## Contributing Modules (Recommended First Contribution)

Modules are the safest way to start contributing. They're self-contained and can't break the core system.

### Module Structure

```
.datacore/modules/your-module/
├── module.yaml           # Module metadata (required)
├── CLAUDE.base.md        # Module context (PUBLIC)
├── CLAUDE.space.md       # Your customizations (SPACE)
├── agents/               # Specialized agents
│   └── your-agent.md
├── commands/             # Slash commands
│   └── your-command.md
└── README.md             # Documentation
```

### Create Your First Module

1. **Create the folder**:
```bash
mkdir -p ~/.datacore/modules/my-first-module
cd ~/.datacore/modules/my-first-module
```

2. **Create module.yaml**:
```yaml
name: my-first-module
version: 0.1.0
description: Brief description of what it does
author: Your Name
tags:
  - category1
  - category2
```

3. **Create CLAUDE.base.md**:
```markdown
# My First Module

## Purpose
What this module does.

## Commands
- `/my-command` - What it does

## Agents
- `my-agent` - What it does
```

4. **Add a simple command** (`commands/hello.md`):
```markdown
# Hello Command

Say hello and confirm the module is working.

## Output
Respond with a friendly greeting and confirm the module is loaded.
```

5. **Test it**:
```bash
claude
> /hello
```

### Module Ideas for New Contributors

| Module | Difficulty | Description |
|--------|------------|-------------|
| `meeting-notes` | Easy | Capture and format meeting notes |
| `code-review` | Easy | PR review checklist and templates |
| `standup` | Easy | Generate daily standup format |
| `research-digest` | Medium | Summarize research findings |
| `metrics-tracker` | Medium | Track and visualize KPIs |

## Org-mode Basics

If you need to edit task files (`*.org`), follow these rules:

### Headings
```org
* Top level (Tier)
** Second level (Focus Area)
*** Task level
```

### Task States
```org
*** TODO Ready to work on
*** NEXT High priority
*** WAITING Blocked
*** DONE Completed
```

### Properties
```org
*** TODO My task
:PROPERTIES:
:CREATED: [2025-12-01 Mon]
:END:
```

See `.datacore/datacore-docs/org-mode-conventions.md` for full details.

## Safe vs Caution Operations

### Safe (Do Freely)
- Read any file
- Add items to `org/inbox.org`
- Create notes in `0-inbox/`
- Run `/today`, `/diagnostic`
- Create modules in `.datacore/modules/`

### Caution (Ask First)
- Edit `next_actions.org`
- Modify agent definitions
- Change command prompts
- Run `./sync push`

### Requires Coordination
- Edit same files as others
- Modify DIPs or specs
- Change space configuration

## Getting Help

1. **Read the docs**: `.datacore/datacore-docs/`
2. **Check CLAUDE.md**: Context for current space
3. **Ask Claude**: Start a session and ask questions
4. **Check DIPs**: `.datacore/dips/` for design decisions

## First Week Checklist

- [ ] Clone your space repository
- [ ] Run `/today` successfully
- [ ] Read root `CLAUDE.md`
- [ ] Read your space's `CLAUDE.md`
- [ ] Understand the layer pattern (base/space/local)
- [ ] Add one item to inbox.org
- [ ] Create a test module
- [ ] Review org-mode conventions doc

## Common Mistakes

### Mistake 1: Editing CLAUDE.md
**Symptom**: Your changes disappear
**Fix**: Edit `.base.md`, `.space.md`, or `.local.md` instead

### Mistake 2: Committing .local.md
**Symptom**: Pre-commit hook blocks you
**Fix**: These files should stay local, add to `.gitignore`

### Mistake 3: Breaking org syntax
**Symptom**: Agents can't parse tasks
**Fix**: Check heading format (`* ` with space), property drawers

### Mistake 4: Wrong git remote
**Symptom**: Pushing space content to wrong repo
**Fix**: Use `./sync` script, check `git remote -v`

## Questions?

- Slack: #datacore-dev
- GitHub: datacore-one/datacore/issues
- Ask Claude in your session

---

*Welcome aboard! Start with a module, and you'll be contributing to core in no time.*
