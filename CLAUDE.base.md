# CLAUDE.md

This file provides guidance to Claude Code when working in this Datacore installation.

## Overview

**Datacore** is a modular AI second brain system built on GTD methodology. This installation contains:

- **0-personal/**: Personal space (GTD, PKM, personal projects)
- **[N]-[name]/**: Team spaces (separate repos)

## Structure

```
~/Data/
├── .datacore/              # Configuration and methodology
│   ├── commands/           # Built-in + module commands
│   ├── agents/             # Built-in + module agents
│   ├── modules/            # Optional modules
│   ├── specs/              # System specifications
│   ├── lib/                # Utility scripts
│   ├── env/                # Secrets (gitignored)
│   └── state/              # Runtime state (gitignored)
├── 0-personal/             # Personal space
│   ├── org/                # GTD system
│   ├── notes/              # Obsidian PKM
│   ├── code/               # Personal projects
│   └── content/            # Generated content
├── [N]-[name]/             # Team spaces (separate repos)
├── install.yaml            # System manifest
└── sync                    # Sync script
```

## Settings

User preferences are configured via YAML files in `.datacore/`:

| File | Purpose | Tracking |
|------|---------|----------|
| `settings.yaml` | Base defaults | Tracked |
| `settings.local.yaml` | User overrides | Gitignored |

**Available settings:**

```yaml
editor:
  open_markdown_on_generate: true  # Open generated .md files in default app
  open_command: ""                 # Custom open command (empty = system default)

sync:
  pull_on_today: true              # Auto-pull repos on /today
  push_on_wrap_up: true            # Auto-push repos on /wrap-up

journal:
  open_after_update: false         # Open journal after updating
```

To customize, create `.datacore/settings.local.yaml` with your overrides.

## Built-in Commands

**Daily Briefing:**
- `/today` - Generate daily briefing with priorities, calendar, AI work summary

**GTD Workflow:**
- `/gtd-daily-start` - Morning planning
- `/gtd-daily-end` - Evening wrap-up and AI delegation
- `/gtd-weekly-review` - Weekly GTD review
- `/gtd-monthly-strategic` - Monthly planning

## Built-in Agents

- `ai-task-executor` - Routes :AI: tagged tasks to specialized agents
- `gtd-inbox-processor` - Inbox entry processing
- `gtd-content-writer` - Blog, email, documentation generation
- `gtd-data-analyzer` - Metrics, reports, insights
- `gtd-project-manager` - Project tracking, blocker escalation
- `gtd-research-processor` - URL analysis, zettel creation
- `conversation-processor` - ChatGPT export processing
- `research-link-processor` - Batch URL processing
- `dip-preparer` - DIP creation, validation, and PR submission
- `session-learning` - Extract patterns and learnings from sessions
- `context-maintainer` - Validate and rebuild CLAUDE.md files

## Optional Modules

Modules extend functionality. Install by cloning to `.datacore/modules/`:

```bash
git clone https://github.com/datacore/module-[name] .datacore/modules/[name]
```

## Working with Spaces

### Personal (0-personal/)

Personal space uses full GTD methodology with direct org-mode access.

**Key locations**:
- `org/inbox.org` - Single capture point
- `org/next_actions.org` - Tasks with :AI: tags for delegation
- `notes/` - Obsidian PKM (journals, pages, knowledge)
- `code/` - Personal projects

**GTD Workflow**:
- inbox.org is sacred - always return to clean state after processing
- AI tasks tagged with :AI: are executed by agents overnight
- Morning briefing shows completed AI work

### Team Spaces ([N]-[name]/)

Team spaces are separate git repos. GitHub Issues are source of truth.

**Key locations**:
- `org/` - Internal AI coordination only
- `today/` - Generated daily briefings
- `research/` - Market research
- `knowledge/` - Shared knowledge
- `projects/` - Code repos

**Team Workflow**:
- GitHub Issues for all team tasks
- org/ routes AI work, creates GitHub issues
- Team members work in GitHub, not org files

## org-mode Conventions

- Heading hierarchy: `*` (one star per level)
- TODO states: TODO, NEXT, WAITING, DONE
- Property drawers: `:PROPERTIES:` ... `:END:`
- Timestamps: `<2025-11-28 Thu>` or `[2025-11-28 Thu]`
- Tags: `:tag1:tag2:`
- Links: `[[link][description]]`

**AI Task Tags**:
- `:AI:` - General AI task
- `:AI:research:` → gtd-research-processor
- `:AI:content:` → gtd-content-writer
- `:AI:data:` → gtd-data-analyzer
- `:AI:pm:` → gtd-project-manager

## Notes Conventions

- Wiki-links: `[[Page Name]]`
- Frontmatter: YAML for journals and clippings
- Journal filename: `YYYY-MM-DD.md`

## Sync

```bash
./sync          # Pull all repos
./sync push     # Commit and push all
./sync status   # Show status
```

## Key Principles

- **Augment, don't replace** - Agents assist, humans decide
- **Progressive processing** - Inbox → triage → knowledge → archive
- **GitHub for teams** - External collaboration via GitHub Issues
- **org-mode for AI** - Internal coordination and task routing
- **Single capture point** - inbox.org, then route and remove

## System Patterns (DIPs)

Datacore follows documented patterns via **Datacore Improvement Proposals (DIPs)**:

| DIP | Pattern | Summary |
|-----|---------|---------|
| [DIP-0001](dips/DIP-0001-contribution-model.md) | Contribution Model | Fork-and-overlay for privacy-safe contributions |
| [DIP-0002](dips/DIP-0002-layered-context-pattern.md) | Layered Context | Four-level privacy for context files |
| [DIP-0010](dips/DIP-0010-external-sync-architecture.md) | External Sync | Bidirectional sync between org-mode and external services |
| [DIP-0009](dips/DIP-0009-gtd-specification.md) | GTD Specification | Complete GTD workflow, agents, and coordination |

### Layered Context Pattern (DIP-0002)

All context files (CLAUDE.md, agents, commands) use layered privacy:

| Layer | Suffix | Visibility | Tracking |
|-------|--------|------------|----------|
| PUBLIC | `.base.md` | Everyone | Tracked (PR to upstream) |
| ORG | `.org.md` | Organization | Tracked in fork |
| TEAM | `.team.md` | Team only | Optional |
| PRIVATE | `.local.md` | Only you | Never tracked |

**Composed file** (`.md`) is generated from layers and gitignored.

```bash
# Rebuild composed CLAUDE.md
python .datacore/lib/context_merge.py rebuild --path .

# Validate no private content in public layers
python .datacore/lib/context_merge.py validate --path .
```

### When Making System Changes

For significant changes, create a DIP:
1. Copy `dips/DIP-0000-template.md`
2. Fill in specification
3. Submit PR to datacore repo
4. Reference DIP in implementation

See `dips/README.md` for full DIP workflow.

## Privacy

See `.datacore/specs/privacy-policy.md` for data classification and sharing guidelines.

## Specifications

System behavior is documented in `.datacore/specs/`:

| Spec | Purpose | Related DIPs |
|------|---------|--------------|
| `privacy-policy.md` | Data classification levels | DIP-0001, DIP-0002 |
| `install-upgrade-process.md` | Installation workflow | - |

---

**This is CLAUDE.base.md** - the PUBLIC layer. Customize by creating:
- `CLAUDE.org.md` - Organization-specific context
- `CLAUDE.local.md` - Personal notes (gitignored)
