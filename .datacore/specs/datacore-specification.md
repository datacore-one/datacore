# Datacore Specification v1.3

A modular, multi-space AI second brain system.

---

## Table of Contents

1. [Overview](#overview)
2. [Core Concepts](#core-concepts)
3. [Architecture](#architecture)
4. [Knowledge Layer](#knowledge-layer)
5. [System Layer](#system-layer)
   - [Task Management (GTD)](#task-management-gtd)
   - [Agents & Commands](#agents--commands)
6. [Configuration](#configuration)
7. [Git & Contribution](#git--contribution)
8. [Operations](#operations)
9. [Integrations](#integrations)
10. [Appendix](#appendix)

---

## Overview

**Datacore** is a personal AI second brain system inspired by Star Trek's Lieutenant Commander Data - an intelligent, efficient knowledge companion that augments cognitive capabilities through systematic information processing, retrieval, and task management.

It provides:

- **Modular architecture** - Core + installable modules (GTD, trading, comms...)
- **Multi-space support** - Personal + unlimited team/project spaces
- **AI-first design** - Built for Claude Code and autonomous agents
- **Git-native** - Everything version controlled, fully reproducible
- **File-based** - Text files, no databases, works with Obsidian/any editor
- **Zettelkasten methodology** - Atomic notes, progressive summarization, emergent connections

### Core Philosophy

- **Data** = The AI agent/advisor (your second brain)
- **Datacore** = The framework, CLI, and configuration system
- **`~/Data`** = Your centralized knowledge repository
- **Spaces** = Isolated contexts (personal, companies, projects)
- **Modules** = Pluggable extensions that encode methodologies and workflows

### Guiding Principles

- **Augment, don't replace** - Agents assist, humans decide
- **Progressive processing** - Inbox → triage → knowledge → archive
- **Cognitive offloading** - Reduce reading time, increase deep work
- **Memory augmentation** - Everything captured, nothing lost
- **Personalization** - The system adapts to individual mental models and decision frameworks

### External Services Principle

**Datacore is the brain. External services are hands.**

```
┌─────────────────────────────────────────────────────────────┐
│                    DATACORE (Orchestrator)                   │
│  1. Maintains best context (RAG, PKM, history)              │
│  2. Delegates execution to specialized services             │
│  3. Receives all results back                               │
│  4. Stores artifacts in knowledge base                      │
│  5. Extracts zettels, updates learning                      │
└─────────────────────────────────────────────────────────────┘
         │                                    ▲
         │ context + instructions             │ results + artifacts
         ▼                                    │
    ┌─────────────────────────────────────────┴───┐
    │         External Services                    │
    │  - PostHog (analytics data)                  │
    │  - n8n (workflow execution, data transport)  │
    │  - Gamma (presentations)                     │
    │  - Figma (design via MCP)                    │
    │  - Specialized AI (image gen, etc.)          │
    └─────────────────────────────────────────────┘
```

**Core rules:**

1. **Datacore has best context** - If an external service has better context, Datacore is failing its job
2. **All artifacts return to Datacore** - Content created by external services is stored in the knowledge base
3. **Published content is knowledge** - Blog posts, tweets, presentations are treated like any other content
4. **Feedback loop is mandatory** - Results inform future context

**When to use external services:**

| Use Case | Example |
|----------|---------|
| Superior execution | n8n workflow for multi-platform posting |
| Specialized capability | Gamma for slides, Figma for design |
| Data provider | PostHog for analytics, Readwise for highlights |
| Transport layer | n8n webhook → inbox.org |

**When NOT to use external services:**

| Use Case | Why |
|----------|-----|
| Decision making | Datacore has full context |
| Content strategy | Needs PKM access |
| Task routing | Needs org-mode and agent knowledge |
| Complex reasoning | Needs cross-space context |

---

## Core Concepts

### Spaces

A **space** is an isolated workspace with its own context, configuration, and content. Every Datacore installation has at least one space (personal).

**Space types:**

| Type | Example | Purpose |
|------|---------|---------|
| Personal | `0-personal/` | Individual GTD, PKM, projects |
| Team | `1-datafund/` | Organization workspace |
| Project | `2-datacore/` | Focused project space |

### The Two Dimensions of a Space

Every space has two distinct dimensions:

```
┌─────────────────────────────────────────────────────────────┐
│                         SPACE                                │
├─────────────────────────────┬───────────────────────────────┤
│       SYSTEM LAYER          │       KNOWLEDGE LAYER          │
│       (Methodology)         │       (Content)                │
├─────────────────────────────┼───────────────────────────────┤
│ .datacore/                  │ notes/, knowledge/             │
│   ├── agents/               │   ├── journals/                │
│   ├── commands/             │   ├── zettel/                  │
│   ├── modules/              │   ├── literature/              │
│   ├── config.yaml           │   ├── topics/                  │
│   └── specs/                │   └── pages/                   │
│                             │                                │
│ org/                        │ content/                       │
│   ├── inbox.org             │   ├── blog/                    │
│   └── next_actions.org      │   ├── reports/                 │
│                             │   └── drafts/                  │
├─────────────────────────────┼───────────────────────────────┤
│ PUBLIC (shareable)          │ PRIVATE (personal data)        │
│ Can contribute upstream     │ Never leaves your machine      │
│ Defines HOW you work        │ Contains WHAT you know         │
└─────────────────────────────┴───────────────────────────────┘
```

**System Layer** (methodology):
- Agents, commands, prompts, workflows
- Configuration and specs
- Task management structure (org files)
- **Shareable**: Can be contributed back to upstream repos
- **Purpose**: Defines *how* work gets done

**Knowledge Layer** (content):
- Notes, journals, zettels, literature
- Generated content (blog posts, reports)
- Personal insights and reflections
- **Private**: Never tracked in shared repos
- **Purpose**: Contains *what* you know and produce

This separation enables:
1. **Sharing methodology** without exposing personal data
2. **Contributing improvements** back to the community
3. **Upgrading system** without losing knowledge
4. **Privacy by design** - clear boundaries

### Modules

Modules are pluggable extensions that encode methodologies and workflows. They live in the System Layer.

**Module structure:**

```
datacore-[name]/
├── module.yaml                # Module manifest
├── CLAUDE.md                  # Module AI context
├── commands/                  # Slash commands
├── agents/                    # Autonomous agents
├── prompts/                   # Reusable prompts
├── workflows/                 # Process documentation
└── templates/                 # File/folder templates
```

**Module manifest (module.yaml):**

```yaml
name: gtd
version: 1.0.0
description: Getting Things Done methodology
author: datacore
repository: https://github.com/datacore-one/datacore-gtd

dependencies:
  - core@>=1.0.0

provides:
  commands:
    - gtd-daily-start
    - gtd-daily-end
    - gtd-weekly-review
  agents:
    - gtd-inbox-processor
    - ai-task-executor
```

**Built-in vs Optional:**

| Built-in | Optional |
|----------|----------|
| Core functionality | Trading workflows |
| GTD methodology | Communications |
| Core agents | DevOps |

GTD is built-in because it defines how Datacore works. Without GTD, there's no methodology.

---

## Architecture

### System Structure

```
~/Data/                                    # Root datacore installation
├── .git/                                  # User's datacore repo
├── .gitignore
├── install.yaml                           # Full system manifest
├── sync                                   # Daily sync script
├── CLAUDE.md                              # Root AI context
│
├── .datacore/                             # System Layer (root)
│   ├── config.yaml                        # Root config
│   ├── specs/                             # Specifications
│   ├── datacore-docs/                     # Documentation
│   ├── agents/                            # Core agents
│   ├── commands/                          # Core commands
│   ├── modules/                           # Installed modules (gitignored)
│   ├── env/                               # Secrets (gitignored)
│   └── state/                             # Runtime state (gitignored)
│
├── 0-personal/                            # Personal space
│   ├── .datacore/                         # System Layer (personal)
│   ├── org/                               # Task management
│   ├── notes/                             # Knowledge Layer
│   ├── code/                              # Projects
│   └── content/                           # Generated outputs
│
├── 1-[team]/                              # Team space (gitignored)
└── 2-[project]/                           # Project space (gitignored)
```

### Personal Space Structure

```
0-personal/
├── CLAUDE.md                              # Personal AI context
│
├── .datacore/                             # SYSTEM LAYER
│   ├── config.yaml
│   ├── modules/                           # Personal modules (gitignored)
│   ├── commands/                          # Personal commands
│   ├── agents/                            # Personal agents
│   ├── env/                               # Personal secrets (gitignored)
│   └── state/                             # Personal state (gitignored)
│
├── org/                                   # TASK MANAGEMENT
│   ├── inbox.org                          # Single capture point
│   ├── next_actions.org                   # GTD next actions
│   ├── someday.org                        # Someday/maybe
│   └── habits.org                         # Habit tracking
│
├── notes/                                 # KNOWLEDGE LAYER
│   ├── journals/                          # Daily journal (YYYY-MM-DD.md)
│   ├── pages/                             # Wiki pages
│   ├── 0-inbox/                           # Unprocessed notes
│   ├── 1-active/                          # Living documents by focus area
│   ├── 2-knowledge/                       # Permanent knowledge
│   │   ├── zettel/                        # Atomic notes
│   │   ├── literature/                    # Source notes
│   │   └── reference/                     # Quick reference
│   └── 3-archive/                         # Historical content
│
├── code/                                  # Personal projects
│   ├── active/
│   ├── experiments/
│   └── archive/
│
└── content/                               # Generated outputs
    ├── blog/
    ├── summaries/
    └── reports/
```

### Team Space Structure

```
[N]-[name]/                                # e.g., 1-datafund/
├── .git/                                  # Space repo
├── CLAUDE.md                              # Space AI context
│
├── .datacore/                             # SYSTEM LAYER
│   ├── config.yaml
│   ├── modules/                           # Space modules (gitignored)
│   ├── commands/                          # Space commands
│   ├── agents/                            # Space agents
│   ├── env/                               # Space secrets (gitignored)
│   └── state/                             # Space state (gitignored)
│
├── org/                                   # TASK MANAGEMENT (internal)
│   ├── inbox.org                          # Internal capture
│   └── next_actions.org                   # AI task queue
│
├── journal/                               # Team daily log
│   └── YYYY-MM-DD.md
│
├── 0-inbox/                               # Unprocessed items
│
├── 1-tracks/                              # ACTIVE WORK
│   ├── ops/                               # Operations
│   ├── product/                           # Product specs
│   ├── dev/                               # Engineering
│   ├── research/                          # Research
│   └── comms/                             # Communications
│
├── 2-projects/                            # Code repos (gitignored)
│
├── 3-knowledge/                           # KNOWLEDGE LAYER
│   ├── topics/                            # Topic pages
│   ├── zettel/                            # Atomic concepts
│   ├── literature/                        # External sources
│   └── reference/                         # People, companies, glossary
│
└── 4-archive/                             # Historical content
```

---

## Knowledge Layer

The Knowledge Layer contains all content - what you know, learn, and produce.

### Zettelkasten Principles

Datacore embraces Zettelkasten methodology:

1. **Atomicity** - Each note captures one concept
2. **Connectivity** - Notes link to related notes
3. **Emergence** - Structure emerges from connections, not folders
4. **Progressive summarization** - Layer highlights for different reading depths

### Note Types

| Type | Location | Purpose | Naming |
|------|----------|---------|--------|
| **Fleeting** | `0-inbox/` | Quick captures | Any |
| **Literature** | `literature/` | Source summaries | `[Source Title].md` |
| **Permanent (Zettel)** | `zettel/` | Atomic concepts | `[Concept Name].md` |
| **Topic** | `topics/` | Aggregated insights | `[Topic Name].md` |
| **Reference** | `reference/` | People, companies, glossary | `[Entity].md` |
| **Index** | Various | Entry points, MOCs | `_index.md` |

### Literature Notes

Created from external sources with progressive summarization:

```markdown
---
title: "Swarm Whitepaper"
author: [Viktor Trón, et al.]
source: https://docs.ethswarm.org/whitepaper
type: literature
created: 2025-11-25
---

# Swarm Whitepaper

## Layer 1: Key Points (30 sec read)
- Decentralized storage incentivized by BZZ token
- Content-addressed chunks with postage stamps
- DISC (Distributed Immutable Store of Chunks)

## Layer 2: Summary (2 min read)
[Condensed overview of main concepts...]

## Layer 3: Detailed Notes
[Full notes with quotes and page references...]

## Connections
- Relates to: [[zettel/Content Addressing]]
- Relates to: [[zettel/Incentive Mechanisms]]
```

### Permanent Notes (Zettels)

Atomic notes capturing single concepts:

```markdown
---
title: Personal Data Sovereignty
type: zettel
created: 2025-11-20
---

# Personal Data Sovereignty

Personal data sovereignty is the principle that individuals have
ultimate control over their personal data...

## Key Aspects
- Ownership: Data belongs to the individual, not platforms
- Control: Granular permissions and consent management
- Portability: Ability to move data between services

## Related
- [[Data Dignity]]
- [[Self-Sovereign Identity]]
- [[GDPR Rights]]

## Sources
- [[literature/Radical Markets - Data as Labor]]
```

### Topic Pages

Topic pages aggregate insights into coherent thematic collections:

```markdown
---
title: Data Marketplace Dynamics
type: topic
created: 2025-11-01
updated: 2025-11-27
---

# Data Marketplace Dynamics

Understanding how data marketplaces function.

## Key Insights

### From Research
- [[literature/McKinsey Report]]: Enterprise data sharing growing 23% YoY
- [[literature/Gartner Analysis]]: 60% of marketplaces fail within 2 years

### Emergent Principles
- [[zettel/Data Liquidity]]
- [[zettel/Trust Bootstrapping Problem]]
- [[zettel/Network Effects in Data Markets]]

## Open Questions
- [ ] Impact of privacy regulations on cross-border data markets?

## Related Topics
- [[Privacy-Preserving Computation]]
- [[Data Sovereignty]]
```

### Page References and Tagging

All connections in the knowledge graph are page references:

| Syntax | Example | Creates/Links To |
|--------|---------|------------------|
| `[[Page]]` | `[[AI Safety]]` | Page "AI Safety" |
| `#tag` | `#strategy` | Page "strategy" |
| `#[[Multi Word]]` | `#[[Data Ownership]]` | Page "Data Ownership" |

**Key insight**: `#tag`, `#[[tag]]`, and `[[page]]` are ALL equivalent.

### Daily Processing Flow

```
Captures (inbox)
       │
       ▼
┌──────────────┐
│ Triage       │  Is this actionable?
│              │  No → Knowledge processing
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Classify     │  What type of knowledge?
│              │  - Source → Literature note
│              │  - Concept → Zettel
│              │  - Reference → Reference note
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Connect      │  What does this relate to?
│              │  - Add wiki-links
│              │  - Update indexes
└──────┬───────┘
       │
       ▼
┌──────────────┐
│ Integrate    │  Where does this surface?
│              │  - Add to relevant MOCs
│              │  - Update project docs
└──────────────┘
```

---

## System Layer

The System Layer contains methodology - how work gets done.

### Task Management (GTD)

Datacore uses Getting Things Done (GTD) methodology powered by org-mode.

#### Core Files

| File | Purpose | Process to Zero? |
|------|---------|------------------|
| `inbox.org` | Single capture point | Yes, daily |
| `next_actions.org` | Actionable tasks organized by context | No |
| `someday.org` | Ideas and future projects | No, review monthly |

#### GTD Workflow

```
1. CAPTURE
   Everything goes to inbox.org
   Don't filter, don't organize - just capture
        │
        ▼
2. CLARIFY
   For each item: What is it? Is it actionable?
   What's the next physical action?
        │
        ▼
3. ORGANIZE
   Route to appropriate destination:
   - Actionable → next_actions.org (by context)
   - Reference → Knowledge Layer
   - Someday → someday.org
   - Delegate to AI → add :AI: tag
   - Trash → delete
        │
        ▼
4. REFLECT
   Daily: Process inbox, review today
   Weekly: Full system review
        │
        ▼
5. ENGAGE
   Work from next_actions.org by context
```

#### Task States

| State | Meaning | Use |
|-------|---------|-----|
| `TODO` | To be done | Default state |
| `NEXT` | Immediate focus | Your current priorities (3-5 max) |
| `WAITING` | Blocked | Waiting on someone/something |
| `DONE` | Completed | Finished tasks |

#### org-mode Syntax

```org
* Inbox
** TODO Call dentist
** Research AI agents
   https://interesting-article.com
** Idea for blog post

* Office                                    # Context
** NEXT Review Q4 budget                    :finance:
** TODO Prepare presentation                :strategy:
** WAITING Response from legal team

* Computer
** TODO Summarize competitor analysis :AI:research:
** TODO Write changelog :AI:content:
```

#### AI Task Delegation

Tag tasks with `:AI:` to delegate to agents:

| Tag | Agent | Output |
|-----|-------|--------|
| `:AI:` | ai-task-executor routes | Varies |
| `:AI:research:` | gtd-research-processor | Literature notes, zettels |
| `:AI:content:` | gtd-content-writer | Blog posts, emails, docs |
| `:AI:data:` | gtd-data-analyzer | Reports, metrics, insights |
| `:AI:pm:` | gtd-project-manager | Status reports, blockers |

**Example AI task:**
```org
** TODO Summarize this paper on knowledge graphs :AI:research:
   Source: https://arxiv.org/abs/xxx
   Extract: Key concepts, methodology, findings
   Output: Literature note + relevant zettels
```

**Result next morning:**
- Literature note in `knowledge/literature/`
- Atomic zettels for key concepts
- Links to related existing notes

#### Daily Rhythm

Commands work in layers - automatable briefings + interactive sessions:

```
DAILY
─────
Morning:
  06:00  /today              (cron) Generates daily briefing
  09:00  /gtd-daily-start    (interactive) Reviews AI work, sets priorities

Evening:
  17:00  /gtd-daily-end      (interactive) Processes inbox, delegates to AI
  18:00  /tomorrow           (interactive) Syncs repos, runs diagnostics

WEEKLY
──────
Friday 16:00  /gtd-weekly-review  (interactive) Comprehensive system review

MONTHLY
───────
Last Friday  /gtd-monthly-strategic  (interactive) Strategic planning
```

**Command Relationships:**

| Command | Type | Purpose |
|---------|------|---------|
| `/today` | Briefing (auto) | Surfaces priorities, calendar, AI work → journal |
| `/gtd-daily-start` | Interactive | Asks for Top 3 priorities, time blocks |
| `/gtd-daily-end` | Interactive | Processes inbox, classifies, delegates |
| `/tomorrow` | System close | Repo sync, diagnostics, set tomorrow's priorities |
| `/gtd-weekly-review` | Interactive | Full system audit, set weekly priorities |

**Morning Flow:**
1. `/today` runs via cron at 6 AM - briefing ready when you wake
2. `/gtd-daily-start` at 9 AM - review briefing, set intentions

**Evening Flow:**
1. `/gtd-daily-end` - process inbox, tag tasks for AI
2. `/tomorrow` - sync repos, run diagnostics, capture priorities for next day

**Weekly Review** (`/gtd-weekly-review` - Friday 4 PM):

The weekly review is the heartbeat of GTD. It maintains system trust.

| Step | Action |
|------|--------|
| 1 | Review week accomplishments |
| 2 | Review AI delegation effectiveness |
| 3 | Process inbox to zero |
| 4 | Review all work areas by category |
| 5 | Follow up on WAITING items (>7 days) |
| 6 | Review all projects (stalled? next actions?) |
| 7 | Review someday/maybe (promote any?) |
| 8 | Check habit completion rates |
| 9 | Preview next week's deadlines and load |
| 10 | Set Top 3 priorities for next week |
| 11 | System reflection (what's working/broken) |
| 12 | Weekly gratitude |

**Without weekly review**, the system degrades:
- Inbox grows unchecked
- WAITING items forgotten
- Projects stall silently
- Trust erodes

**Typical Usage:**

| User Type | Morning | Evening | Weekly |
|-----------|---------|---------|--------|
| Minimal | `/today` | `/tomorrow` | `/gtd-weekly-review` |
| Standard | `/today` → `/gtd-daily-start` | `/gtd-daily-end` → `/tomorrow` | `/gtd-weekly-review` |
| GTD Purist | `/gtd-daily-start` | `/gtd-daily-end` | `/gtd-weekly-review` |

#### Personal vs Team Task Management

| Aspect | Personal | Team Space |
|--------|----------|------------|
| **Primary system** | org-mode directly | GitHub Issues (external) |
| **org-mode role** | Main task system | AI preprocessing layer |
| **Visibility** | Private | Team-visible via GitHub |
| **AI tasks** | Processed overnight | Routed appropriately |

**Team spaces**: org-mode acts as an AI work assistant:
- Captures go to `inbox.org` or conversational interface
- AI classifies and routes:
  - Public tasks → GitHub Issues
  - Sensitive tasks → org only
  - AI-doable → next_actions.org with :AI: tag
- Users see GitHub; org stays invisible

```
User: "Create a task to review competitor pricing"
        │
        ▼
AI classifies:
- Public? → GitHub Issue
- Sensitive? → org only
- AI-doable? → next_actions.org :AI:
        │
        ▼
AI responds: "Created. Added to Q1 research sprint."
```

### Agents & Commands

Datacore uses two types of AI automation:

| Type | Trigger | Interaction | Use Case |
|------|---------|-------------|----------|
| **Commands** | `/command-name` | Interactive | User-initiated workflows |
| **Agents** | `:AI:tag:` in tasks | Autonomous | Background processing |

### Command Design Principles

1. **Automatable** - Can be triggered via cron
2. **Idempotent** - Safe to run multiple times
3. **Self-contained** - All context in the definition
4. **Composable** - Can be chained
5. **Observable** - Output goes to files
6. **Conversational** - Guide users through options

### Conversational Command Style

Module commands should be **conversational**, not CLI wrappers:

**DO:**
- Ask user what they want if intent unclear
- Guide through options with numbered choices
- Provide context and explanations
- Handle errors gracefully with suggestions
- Respect user settings for auto-actions

**DON'T:**
- Require user to memorize CLI parameters
- Fail silently on missing arguments
- Dump raw CLI output without formatting

**Example flow:**
```
User: /datacortex
Agent: "What would you like to do with your knowledge graph?"
       1. Explore - Open visualization in browser
       2. Stats - Show graph statistics
       3. Find orphans - List unlinked documents
User: 1
Agent: "Opening graph visualization... [opens browser]"
       "Visualization ready at http://localhost:8765"
```

**Auto-run settings:** Commands can offer `auto_*` settings in their module.yaml for power users who want to skip prompts. Example: `datacortex.auto_serve: true` skips the menu and opens the graph immediately.

**Cron examples:**
```bash
# Morning briefing at 6 AM
0 6 * * * cd ~/Data && claude -p "/today"

# Weekly review Sunday evening
0 18 * * 0 cd ~/Data && claude -p "/gtd-weekly-review"

# Nightly AI task processing
0 2 * * * cd ~/Data && claude -p "Run ai-task-executor agent"
```

### Command Resolution

Commands resolve in priority order (highest to lowest):

```
1. Space custom:     [N]-[name]/.datacore/commands/
2. Space modules:    [N]-[name]/.datacore/modules/*/commands/
3. Personal custom:  0-personal/.datacore/commands/
4. Personal modules: 0-personal/.datacore/modules/*/commands/
5. Root custom:      .datacore/commands/
6. Root modules:     .datacore/modules/*/commands/
```

### Agent Architecture

```
                    Task Sources
                         │
        ┌────────────────┼────────────────┐
        │                │                │
   org-mode         GitHub Issues     Team Inbox
   (:AI: tags)      (automation       (shared
        │            labels)          captures)
        │                │                │
        └────────────────┼────────────────┘
                         │
                         ▼
                 ┌───────────────┐
                 │ ai-task-      │
                 │ executor      │
                 │ (router)      │
                 └───────┬───────┘
                         │
        ┌────────────────┼────────────────┐
        │                │                │
        ▼                ▼                ▼
   ┌─────────┐    ┌─────────┐    ┌─────────┐
   │research │    │content  │    │data     │
   │processor│    │writer   │    │analyzer │
   └─────────┘    └─────────┘    └─────────┘
        │                │                │
        ▼                ▼                ▼
   literature/      content/         reports/
   zettel/          drafts/          insights/
```

### Coordinator-Subagent Pattern

For complex batch operations, use the coordinator-subagent pattern:

```
                 ┌─────────────────────┐
                 │ gtd-inbox-          │
                 │ coordinator         │
                 │ (orchestrator)      │
                 └─────────┬───────────┘
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
    ┌──────────┐    ┌──────────┐    ┌──────────┐
    │ inbox-   │    │ inbox-   │    │ inbox-   │
    │ processor│    │ processor│    │ processor│
    └──────────┘    └──────────┘    └──────────┘
          │                │                │
          └────────────────┼────────────────┘
                           │
                           ▼
                    Aggregated results
```

**Benefits:**
- Parallelization (spawn multiple processors)
- Clean separation of concerns
- Robust error handling per entry

### Space-Aware Reviews

Weekly and monthly reviews adapt to space context:

| Context | Review Focus |
|---------|--------------|
| **Personal** | Focus areas (TIER 1/2/3), individual habits, personal goals |
| **Organization** | Team assignments, cross-project dependencies, GitHub integration |

### Agent Output Pattern

All agent outputs go through inbox for human review:

```
1. AGENT RUNS
   Agent generates output (report, draft, analysis)
        │
        ▼
2. OUTPUT TO INBOX
   Save to: [space]/0-inbox/[type]-[date]-[name].md
        │
        ▼
3. INBOX PROCESSING
   During /today or /gtd-daily-start:
   - Summarize each item for daily briefing
   - Flag items needing action
        │
        ▼
4. ARCHIVE AFTER PROCESSING
   Move to appropriate destination
```

**Output file naming:** `[type]-[YYYY-MM-DD]-[name].md`

**Types:** `report-`, `draft-`, `research-`, `analysis-`, `summary-`

### Today Command

"Today" is the primary touchpoint - a generated summary that surfaces everything relevant:

**Personal Today:**
- Priority tasks
- Calendar
- Overnight AI work
- Decisions needed
- Health & habits

**Space Today:**
- Team status
- Today's priorities
- GitHub activity
- Standup preview
- Decisions pending

### Journal Format

**Personal Journal** (`0-personal/notes/journals/YYYY-MM-DD.md`):
```markdown
---
date: 2025-11-27
type: journal
---

## Morning
- Focus: [main focus for the day]
- Energy: [High/Medium/Low]

## Log
- 09:00 - [activity]
- 11:00 - [activity]

## Reflections
- [insights, learnings]

## Tomorrow
- [priorities for next day]
```

**Team Journal** (`[N]-[name]/journal/YYYY-MM-DD.md`):

Team journals aggregate work from multiple contributors, organized by project for easy scanning.

```markdown
---
date: 2025-11-27
type: team-journal
space: datafund
contributors: [gregor, tfius, crt]
---

# 2025-11-27

## [Project Name]

### @username - Brief Description (HH:MM)

**Goal:** What they were trying to accomplish

**Accomplished:**
- Item 1
- Item 2

**Files Modified:**
- path/to/file.ts

**Commits:** `abc1234`, `def5678`
**Issues:** #12, #13

---

## [Another Project]

### @another_user - Their Work (HH:MM)
...
```

**Attribution Rules:**
- Use GitHub username with `@` prefix (e.g., `@tfius`, `@plur9`)
- Group entries by **project first**, then by contributor
- Include commit hashes and issue numbers when available
- Time in parentheses is session start time (optional)

**Frontmatter:**
- `contributors`: List of GitHub usernames who contributed that day

## Key Decisions
- [decision 1]

## Blockers
- [blocker or "None"]
```

---

## Configuration

### install.yaml (System Manifest)

The complete blueprint for reconstructing the entire system:

```yaml
meta:
  name: "User's Datacore"
  root: ~/Data
  version: 1.0.0

modules:
  - repo: datacore-one/datacore-gtd
    path: .datacore/modules/gtd

personal:
  modules:
    - repo: datacore-one/datacore-trading
      path: 0-personal/.datacore/modules/trading

spaces:
  datafund:
    repo: datafund/space
    path: 1-datafund
    modules:
      - repo: datacore-one/datacore-comms
    projects:
      - repo: datafund/verity
        path: 2-projects/verity
```

### .datacore/config.yaml (Space Config)

```yaml
space:
  name: datafund
  type: team                               # personal | team | project

modules:
  installed:
    - name: comms
      repo: datacore-one/datacore-comms
      version: 1.0.0

settings:
  obsidian_vault: ./
  default_branch: main
  auto_sync: true
```

### CLAUDE.md Assembly

Claude contexts are assembled from multiple sources:

```
~/Data/CLAUDE.md                           # Root context
├── @include .datacore/modules/*/CLAUDE.md # Module contexts
├── @include 0-personal/CLAUDE.md          # Personal context
└── @include 1-datafund/CLAUDE.md          # Space contexts
```

### Environment & Secrets

**`.datacore/env/`** (gitignored):
```
.datacore/env/
├── .env                    # Environment variables
├── .env.local              # Local overrides
└── credentials/            # API keys, tokens
```

**`.datacore/state/`** (gitignored):
```yaml
# .datacore/state/variables.yaml
last_sync: 2025-11-27T10:00:00Z
session:
  current_focus: verity
stats:
  notes_count: 1547
```

---

## Git & Contribution

### Repository Strategy

**Critical distinction:**

| Repository Type | Contains | Personal Data? | Shareable? |
|-----------------|----------|----------------|------------|
| `datacore-one/datacore` | Bootstrap + GTD | No | Yes (fork) |
| `datacore-one/datacore-*` | Optional modules | No | Yes (install) |
| `[user]/datacore` | User's fork + personal | Yes | No (private) |
| `[org]/space` | Team content | Team data | Team only |

**Modules are methodology (System Layer), not data (Knowledge Layer).**

### Repository Hierarchy

```
Level 0: Framework (Public)
└── datacore-one/datacore              # Bootstrap - users fork this

Level 1: Modules (Public)
├── datacore-one/datacore-gtd          # GTD methodology
├── datacore-one/datacore-trading      # Trading workflows
└── datacore-one/datacore-comms        # Communications

Level 2: User Fork (Private)
└── [user]/datacore                    # Personal content + config

Level 3: Spaces (Team repos)
├── datafund/space → 1-datafund/
└── ...

Level 4: Projects (Gitignored by spaces)
├── datafund/verity
└── ...
```

### Privacy & Data Classification

| Level | Description | Example |
|-------|-------------|---------|
| **PUBLIC** | In repo, shareable | Agents, commands, specs |
| **TEAM** | Within team spaces | Space content |
| **PRIVATE** | Never leaves machine | Journals, personal notes |

**Gitignore pattern:**
```gitignore
# Knowledge Layer (private)
**/*.org
**/journals/
**/pages/
**/*.db
install.yaml
CLAUDE.md

# Team spaces (separate repos)
/[1-9]-*/

# Modules (cloned on install)
.datacore/modules/
```

### Contributing Back

The two-dimension model enables contribution:

**What CAN be contributed (System Layer):**
- Improved agents
- New commands
- Bug fixes in prompts
- Workflow documentation
- Module enhancements

**What CANNOT be contributed (Knowledge Layer):**
- Personal notes
- Journal entries
- Private insights
- Organization-specific content

**Contribution workflow:**

```
1. IDENTIFY IMPROVEMENT
   While using Datacore, you improve an agent or command
        │
        ▼
2. ISOLATE CHANGE
   The improvement is in .datacore/ (System Layer)
   Not in notes/ or org/ (Knowledge Layer)
        │
        ▼
3. SUBMIT UPSTREAM
   Create PR to datacore-one/datacore or module repo
   PR contains ONLY System Layer changes
        │
        ▼
4. COMMUNITY BENEFITS
   Merged improvements available to all users
   Your personal data remains private
```

**Example: Improving an agent**

```bash
# Your improvement is in:
.datacore/agents/gtd-research-processor.md

# Create branch in your fork
git checkout -b improve-research-processor

# Commit only the agent file
git add .datacore/agents/gtd-research-processor.md
git commit -m "Improve research processor citation handling"

# Push and create PR to upstream
git push origin improve-research-processor
# → Create PR to datacore-one/datacore
```

### Template vs Local Files

| Template (tracked) | Local (gitignored) |
|-------------------|-------------------|
| `CLAUDE.template.md` | `CLAUDE.md` |
| `install.yaml.example` | `install.yaml` |

On upgrade:
```bash
git pull origin main
diff CLAUDE.md CLAUDE.template.md
# Manually merge relevant changes
```

---

## Operations

### Installation

```bash
# 1. Clone datacore bootstrap
git clone https://github.com/datacore-one/datacore.git ~/Data
cd ~/Data

# 2. Copy templates to local versions
cp CLAUDE.template.md CLAUDE.md
cp install.yaml.example install.yaml

# 3. Run Claude Code for personalization
claude
> "Set up my datacore"
```

### Upgrades

```bash
# Pull upstream changes
git pull origin main

# Compare local to updated template
diff CLAUDE.md CLAUDE.template.md

# Manually merge relevant changes
```

### Sync

```bash
./sync                  # Pull all repos
./sync push             # Commit and push all
./sync status           # Show status
```

The sync script handles:
- Root repo
- All installed modules
- All numbered spaces
- All projects within spaces

### Archiving

**Core principles:**
1. Single archive location per space: `3-archive/` or `4-archive/`
2. Mirror folder structure
3. Version linking between active and archived
4. No nested `archive/` subfolders

**When to archive:**
- Superseded by newer version
- Project/initiative completed
- Approach deprecated
- Historical reference only

**Archive structure mirrors source:**
```
4-archive/
├── 1-tracks/
│   └── dev/
│       └── architecture/
│           └── Specification-v1.md
└── 3-knowledge/
    └── literature/
        └── old-research.md
```

**What NOT to archive:**
- Zettels (evolve, don't archive)
- Journals (historical by nature)
- Active docs
- Literature notes

### Asset Management

Assets live alongside their content:

```
product/verity/
├── roadmap.md
├── specs/
│   ├── api-spec.md
│   └── assets/
│       └── api-flow.png
└── assets/
    └── logo.png
```

**Convention:**
- `assets/` subfolder next to related content
- Reference via relative paths: `![diagram](./assets/flow.png)`
- Prefer text formats (Mermaid, SVG) over binary

### Access Control

| Path | Personal | Team | External |
|------|----------|------|----------|
| `0-personal/` | Full | None | None |
| `[N]-[name]/` | Full | Full | None |
| `[N]-[name]/2-projects/` | Full | Full | Configurable |

---

## Integrations

### External Sources

Not all knowledge lives in Datacore. The goal is to make external resources **findable** and **linkable**.

**Pattern 1: Index Pages**

Create indexes that catalog external resources:

```markdown
# External Resources Index

## Team Tools

### Notion
- **Workspace**: [Datafund Team](https://notion.so/datafund)
- **Key databases**: Product Backlog, Meeting Notes, OKRs

### Google Drive
- **Shared Drive**: [Datafund](https://drive.google.com/...)
- **Key folders**: Contracts, Presentations, Legal
```

**Pattern 2: Sync Artifacts**

For important external content, create local reference notes with `type: external-reference`.

**Pattern 3: Import Workflows**

```org
*** TODO Import pricing research from Notion :AI:research:
    Source: https://notion.so/datafund/pricing-research
    Output: ~/Data/1-datafund/research/market/pricing-analysis.md
```

### Supported Integrations

| Source | Integration Type |
|--------|------------------|
| **GitHub** | Native via `gh` CLI |
| **Notion** | Link index, API sync (planned) |
| **Google Docs/Drive** | Link index, manual import |
| **Figma** | Link index, MCP tool |
| **Linear/Jira** | Link index, API sync (planned) |
| **Readwise** | Auto-sync to literature/ (planned) |
| **Email/Telegram** | Capture to inbox (via n8n) |

### External Sync (DIP-0010)

Bidirectional sync between org-mode and external services. Org-mode serves as the internal coordination layer and source of truth; external services provide familiar UIs for humans.

**Abstract Payload Architecture:**

The sync infrastructure is payload-agnostic. Different adapters sync different content types:

| Adapter | Org File | Content Type | External Service |
|---------|----------|--------------|------------------|
| GitHub | `next_actions.org` | Tasks | GitHub Issues |
| Calendar | `calendar.org` | Calendar entries | Google Calendar |
| Asana | `next_actions.org` | Tasks | Asana Tasks |

**Architecture:**

```
┌─────────────────────────────────────────────────────────────┐
│                      Sync Engine                             │
│  .datacore/lib/sync/                                        │
│  ├── engine.py      # Orchestration                         │
│  ├── router.py      # Entry routing rules                   │
│  ├── history.py     # SQLite sync history                   │
│  ├── conflict.py    # Conflict detection/resolution         │
│  └── adapters/      # Service-specific adapters             │
│      ├── base.py    # SyncAdapter interface (abstract)      │
│      ├── github.py  # GitHub Issues (tasks)                 │
│      └── calendar.py # Google Calendar (calendar entries)   │
└─────────────────────────────────────────────────────────────┘
```

**Implementation Status:**

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 1 | GitHub adapter, sync engine, router | ✅ Complete |
| Section 11 | Tag Governance (registry, validator, diagnostic) | ✅ Complete |
| Phase 2 | Conflict resolution (detection, strategies, queue) | ✅ Complete |
| Phase 3 | Calendar adapter, calendar.org | 🔄 Next |
| Phase 4 | Additional adapters (Asana, Linear) | Future |

**Key files:**
- Tag registry: `.datacore/config/tags.yaml`
- Tag validator: `.datacore/lib/tag_validator.py`
- Conflict resolution: `.datacore/lib/sync/conflict.py`
- Sync command: `/sync`

**See:** [DIP-0010: External Sync Architecture](../dips/DIP-0010-external-sync-architecture.md)

### n8n Workflow Bridges

```
External Tools          n8n Workflows           Datacore
─────────────          ─────────────           ────────

Notion webhook    →    notion-sync       →    knowledge/
Email forward     →    email-capture     →    0-inbox/
Telegram bot      →    telegram-capture  →    0-inbox/
Readwise API      →    readwise-sync     →    literature/
Calendar          →    calendar-digest   →    journal/
```

---

## Appendix

### Repository List

**Core:**
| Repo | Purpose |
|------|---------|
| `datacore-one/datacore` | Bootstrap, core agents/commands |
| `datacore-one/datacore-gtd` | GTD methodology |
| `datacore-one/datacore-trading` | Trading workflows |
| `datacore-one/datacore-comms` | Communications |
| `datacore-one/datacore-org` | Organization template |
| `datacore-one/datacore-space` | Datacore dev space |
| `datacore-one/datacore-dips` | Improvement Proposals |

**User:**
| Repo | Purpose |
|------|---------|
| `[user]/datacore` | User's fork (personal content) |
| `[user]/module-*` | User's private modules |

**Organization:**
| Repo | Purpose |
|------|---------|
| `[org]/space` | Organization's space |
| `[org]/[project]` | Project repos |

### Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.3.1 | 2025-12-01 | Added coordinator-subagent pattern, space-aware reviews, enhanced /today with journal insights, enhanced /tomorrow with session learning |
| 1.3.0 | 2025-12-01 | Added Task Management (GTD) section with org-mode workflow, AI delegation, clarified command relationships (/today, /tomorrow, /gtd-daily-*) |
| 1.2.0 | 2025-12-01 | Restructured with logical grouping, added Two Dimensions concept, added Contribution workflow |
| 1.1.0 | 2025-12-01 | Consolidated operational specs |
| 1.0.0 | 2025-11-27 | Initial specification |

---

*Datacore - A modular AI second brain system*
*Last updated: 2025-12-01*
