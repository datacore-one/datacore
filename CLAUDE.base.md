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

## Commands

<!-- REGISTRY:commands -->

## Agents

<!-- REGISTRY:agents -->

## Installed Modules

<!-- REGISTRY:modules -->

## MCP Sources & Services

<!-- REGISTRY:sources -->

## Infrastructure

<!-- REGISTRY:infrastructure -->

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
- `:AI:research:` → research-orchestrator
- `:AI:content:` → gtd-content-writer
- `:AI:data:` → gtd-data-analyzer
- `:AI:pm:` → gtd-project-manager
- `:AI:technical:` → CTO queue (human review required)

## Notes Conventions

- Wiki-links: `[[Page Name]]`
- Frontmatter: YAML for journals and clippings
- Journal filename: `YYYY-MM-DD.md`
- **Tags**: Inline `#tag` format at end of content (NOT frontmatter arrays)

## Tag System

Tags are core to Datacore - enabling holistic views of projects, cross-system queries, and unified reporting.

**Registries**:
- `.datacore/tags.yaml` - System-wide reserved tags
- `[space]/.datacore/tags.yaml` - Space-specific tags

**Format by system**:
- Org-mode: `:tag1:tag2:` (hierarchical, e.g., `:project:ops:legal:`)
- PKM/CRM: `#tag` inline at end of content
- Frontmatter: Single values only (`type: zettel`), NOT arrays

See [DIP-0014](dips/DIP-0014-tag-taxonomy.md) for full specification.

## Sync

```bash
./sync          # Pull all repos
./sync push     # Commit and push all
./sync status   # Show status
```

## Bash Usage

- **Never use multi-line Bash commands.** Chain with `&&` or make separate tool calls.
- Use dedicated tools instead of Bash: `Glob` not `ls`/`find`, `Read` not `cat`/`sed`/`head`/`tail`, `Grep` not `grep`/`rg`.

## Key Principles

- **Augment, don't replace** - Agents assist, humans decide
- **Progressive processing** - Inbox → triage → knowledge → archive
- **GitHub for teams** - External collaboration via GitHub Issues
- **org-mode for AI** - Internal coordination and task routing
- **Single capture point** - inbox.org, then route and remove

## System Patterns (DIPs)

Datacore follows documented patterns via **Datacore Improvement Proposals (DIPs)**:

### Core Infrastructure

| DIP | Pattern | Summary |
|-----|---------|---------|
| [DIP-0001](dips/DIP-0001-contribution-model.md) | Contribution Model | Fork-and-overlay for privacy-safe contributions |
| [DIP-0002](dips/DIP-0002-layered-context-pattern.md) | Layered Context | Four-level privacy for context files |
| [DIP-0014](dips/DIP-0014-tag-taxonomy.md) | Tag Taxonomy | Unified tag system, namespaces, formats, registries |
| [DIP-0016](dips/DIP-0016-agent-registry.md) | Agent Registry | Agent/command discoverability, registries, context patterns |

### Knowledge & Content

| DIP | Pattern | Summary |
|-----|---------|---------|
| [DIP-0003](dips/DIP-0003-scaffolding-pattern.md) | Scaffolding Pattern | Knowledge base structure and organization |
| [DIP-0004](dips/DIP-0004-knowledge-database.md) | Knowledge Database | Datacortex, embeddings, semantic search |
| [DIP-0015](dips/DIP-0015-semantic-organization.md) | Semantic Organization | File handling, Git LFS, ingest workflow |
| [DIP-0017](dips/DIP-0017-outbox-archive-pattern.md) | Outbox & Archive | Content routing out, archive to server |
| [DIP-0019](dips/DIP-0019-learning-architecture.md) | Learning Architecture | Three-loop learning: capture, absorption, user learning |
| [DIP-0021](dips/DIP-0021-search-research-architecture.md) | Search & Research | Three-layer search/research/ingest with pluggable sources |

### GTD & Task Management

| DIP | Pattern | Summary |
|-----|---------|---------|
| [DIP-0009](dips/DIP-0009-gtd-specification.md) | GTD Specification | Complete GTD workflow, agents, and coordination |
| [DIP-0010](dips/DIP-0010-external-sync-architecture.md) | External Sync | Bidirectional sync between org-mode and external services |
| [DIP-0011](dips/DIP-0011-nightshift-module.md) | Nightshift Module | Overnight AI task execution and evaluation |

### Domain Modules

| DIP | Pattern | Summary |
|-----|---------|---------|
| [DIP-0012](dips/DIP-0012-crm-module.md) | CRM Module | Contact relationship management |
| [DIP-0013](dips/DIP-0013-meetings-module.md) | Meetings Module | Meeting preparation, transcription, follow-up |

### Registries

Agent and command discovery uses central registries (per DIP-0016):

| Registry | Purpose |
|----------|---------|
| `.datacore/registry/agents.yaml` | All agents with skills, triggers, relationships |
| `.datacore/registry/commands.yaml` | All commands with invocations, hooks |
| `.datacore/registry/sources.yaml` | Pluggable search/research source providers (DIP-0021) |

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
1. **Create separate branch**: `git checkout -b dip-XXXX-description`
2. Copy `dips/DIP-0000-template.md`
3. Fill in specification
4. Submit PR to datacore repo from feature branch
5. Reference DIP in implementation

**Important**: Each DIP must be on its own feature branch to enable independent review and discussion.

See `dips/README.md` for full DIP workflow.

## Privacy

See `.datacore/specs/privacy-policy.md` for data classification and sharing guidelines.

## Specifications

DIPs are the primary specification format. The `specs/` directory contains:

| Spec | Purpose | Status |
|------|---------|--------|
| `datacore-specification.md` | System overview and architecture | Overview (details in DIPs) |
| `privacy-policy.md` | Data classification levels | Active (ref: DIP-0001, DIP-0002) |

**Note**: For detailed specifications, always reference the relevant DIP. The main specification provides overview context; DIPs provide authoritative details.

---

**This is CLAUDE.base.md** - the PUBLIC layer. Customize by creating:
- `CLAUDE.org.md` - Organization-specific context
- `CLAUDE.local.md` - Personal notes (gitignored)
