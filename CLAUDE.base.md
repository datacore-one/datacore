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

## Privacy

See `.datacore/specs/privacy-policy.md` for data classification and sharing guidelines.

---

**Setup**: Copy this file to `CLAUDE.md` and customize for your installation.
