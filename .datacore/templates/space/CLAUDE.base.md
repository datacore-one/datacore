# [Organization Name] Space

> **TODO**: Replace this template with your organization's context.

## Overview

Brief description of your organization/team.

## Key Projects

- **Project A**: Description
- **Project B**: Description

## Team

- Person 1 - Role
- Person 2 - Role

## Important Links

- Website: https://...
- GitHub: https://github.com/...
- Slack/Discord: ...

## Current Focus

What the team is focused on right now.

## Structure

```
[N]-[name]/                       # e.g., 1-teamspace/
├── CLAUDE.md                     # Composed context (gitignored)
├── CLAUDE.base.md                # PUBLIC layer - generic template
├── CLAUDE.space.md               # SPACE layer - space customizations
├── CLAUDE.local.md               # PRIVATE layer - personal notes
├── _index.md                     # Space overview and navigation
│
├── .datacore/                    # System configuration
│   ├── config.yaml               # Space settings
│   ├── commands/                 # Space-specific commands
│   ├── agents/                   # Space-specific agents
│   ├── learning/                 # AI learning (patterns, corrections)
│   ├── state/                    # Runtime state (gitignored)
│   └── env/                      # Secrets (gitignored)
│
├── org/                          # GTD task management
│   ├── inbox.org                 # Team task capture
│   └── next_actions.org          # AI task queue
│
├── 0-inbox/                      # Unprocessed notes (process to zero)
│
├── journal/                      # Team activity log (daily entries)
│   └── YYYY-MM-DD.md             # Daily journal = today's briefing
│
├── 1-tracks/                     # Active work by track/department
│   ├── _index.md                 # Track overview
│   ├── ops/                      # Operations, OKRs
│   ├── product/                  # Product specs, roadmaps
│   ├── dev/                      # Engineering docs
│   ├── research/                 # Market research
│   └── comms/                    # Marketing, content
│
├── 2-projects/                   # Code repositories (gitignored)
│   └── _index.md
│
├── 3-knowledge/                  # Shared knowledge (Zettelkasten)
│   ├── _index.md                 # Knowledge navigation
│   ├── insights.md               # Organizational insights
│   ├── pages/                    # General wiki pages
│   ├── zettel/                   # Atomic concepts
│   ├── literature/               # Source summaries
│   ├── reference/                # People, companies, glossary
│   └── [topic]-docs/             # Topic documentation
│
└── 4-archive/                    # Historical content
    └── _index.md
```

## Numbered Folder Convention

Numbers indicate processing stage:
- `0-` = Capture/inbox
- `1-` = Active work (tracks/departments)
- `2-` = Projects (code repos)
- `3-` = Permanent knowledge
- `4-` = Archive

## Agents

Core agents available in this space:

| Agent | Purpose |
|-------|---------|
| `org-coordinator` | Routes tasks, manages track handoffs |
| `standup-generator` | Compiles async team standups |

**AI Task Tags:**
```org
* TODO Research competitor X :AI:research:
* TODO Draft announcement :AI:content:
* TODO Generate metrics report :AI:data:
* TODO Track project status :AI:pm:
```

## AI Learning

Agents continuously improve through `.datacore/learning/`:

| File | Purpose |
|------|---------|
| `patterns.md` | Successful approaches to remember |
| `corrections.md` | Human feedback log |
| `preferences.md` | Org style and preferences |

**How it works:**
1. Agents read patterns/preferences before tasks
2. Agents apply learned approaches
3. Human feedback logged to corrections
4. Patterns extracted and updated

## Commands

Available space-specific commands:
- `/standup` - Generate team standup
- (Add your custom commands here)

## Index Files

Every major folder has `_index.md` for navigation:
- Status summaries
- Recent updates
- Links to key content
- Entry points for exploration

**Key indexes:**
- `_index.md` - Space home page
- `1-tracks/_index.md` - Track overview
- `3-knowledge/_index.md` - Knowledge entry point

## Journal Sync

Team members sync updates from personal journals:

**Location:** `journal/YYYY-MM-DD.md`

**How it works:**
1. Team member works on space-related tasks
2. Updates recorded in personal journal (`0-personal/journal/`)
3. During `/gtd-daily-end`, relevant updates sync here
4. Format: `## Updates from @[username]`

## Knowledge Management (Zettelkasten)

The knowledge base uses Zettelkasten methodology:

**Pages** (`3-knowledge/pages/`)
- General wiki pages for concepts, processes
- Longer documents, not atomic

**Zettel** (`3-knowledge/zettel/`)
- Atomic notes capturing single concepts
- Self-contained, linked to related ideas

**Literature** (`3-knowledge/literature/`)
- Source summaries with progressive summarization
- Links to extracted zettels

**Reference** (`3-knowledge/reference/`)
- Quick lookup: people, companies, glossary

**Insights** (`3-knowledge/insights.md`)
- Organizational patterns and discoveries
- Cross-track connections
- Strategic observations

## Capture Points

| What | Where |
|------|-------|
| Tasks | `org/inbox.org` |
| Notes/Ideas | `0-inbox/` |
| Quick capture | Either, then process |

**Goal:** Process both inboxes to zero regularly.

## GTD Workflow

**Team Inbox:** `org/inbox.org`
- Single capture point for team tasks
- Processed by `org-coordinator` agent

**Next Actions:** `org/next_actions.org`
- Organized by track
- `:AI:` tags for automation
- `Waiting For` section for human approvals

**Task States:** TODO, NEXT, WAITING, DONE

## Human Escalation

Agents escalate to humans for:
- Strategic decisions
- External communications (final approval)
- Budget/resource allocation
- Items flagged as "needs human judgment"
- Blockers older than 7 days

## Getting Started

1. Replace this template with your organization's context
2. Update `_index.md` with team overview
3. Update `1-tracks/ops/overview.md` with mission, values
4. Configure `.datacore/config.yaml`
5. Customize `.datacore/learning/preferences.md`
6. Start capturing to `org/inbox.org`

## Parent Context

For full Datacore documentation: `~/Data/CLAUDE.md`
