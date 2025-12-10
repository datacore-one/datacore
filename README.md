# Datacore

**Your mind, extended.**

Datacore is your second self - an AI extension that learns what you know, handles what drains you, and amplifies what makes you unique.

Not AI that replaces you. AI that extends you.

## What is Datacore?

Datacore is an open-source AI second brain system built on GTD (Getting Things Done) methodology. It creates extensions of yourself through AI agents aligned to your knowledge, goals, and workflows.

- **Knowledge-centered**: Your expertise becomes leverage
- **Autonomous operations**: Systems that work while you rest
- **Human-first**: Technology serving human flourishing

## Structure

```
~/Data/
├── .datacore/                    # System core
│   ├── agents/                   # AI agents (11 built-in)
│   ├── commands/                 # Slash commands (16 built-in)
│   ├── specs/                    # System specifications
│   ├── lib/                      # Utility scripts
│   └── modules/                  # Optional extensions
│
├── 0-personal/                   # Personal space
│   ├── org/                      # GTD system (org-mode)
│   │   ├── inbox.org             # Single capture point
│   │   ├── next_actions.org      # Tasks with :AI: delegation
│   │   ├── someday.org           # Someday/maybe
│   │   └── habits.org            # Habit tracking
│   ├── notes/                    # PKM (Obsidian/Logseq)
│   │   ├── journals/             # Daily journal
│   │   ├── pages/                # Wiki pages
│   │   ├── 0-inbox/              # Note inbox
│   │   ├── 1-active/             # Active focus areas
│   │   ├── 2-knowledge/          # Permanent knowledge
│   │   └── 3-archive/            # Historical
│   ├── code/                     # Personal projects
│   └── content/                  # AI-generated content
│
├── [N]-[name]/                   # Team spaces (separate repos)
│
├── CLAUDE.md                     # AI instructions (from template)
├── install.yaml                  # Installation manifest
└── sync                          # Multi-repo sync script
```

## Built-in Agents

| Agent | Purpose |
|-------|---------|
| `ai-task-executor` | Routes :AI: tagged tasks to specialized agents |
| `gtd-inbox-processor` | Processes inbox entries with GTD methodology |
| `gtd-content-writer` | Generates blog posts, emails, documentation |
| `gtd-data-analyzer` | Creates reports, metrics, insights |
| `gtd-project-manager` | Tracks projects, escalates blockers |
| `gtd-research-processor` | Analyzes URLs, creates literature notes |
| `conversation-processor` | Extracts knowledge from ChatGPT exports |
| `research-link-processor` | Batch URL processing |
| `archiver` | Manages content archival with version linking |

## Built-in Commands

**GTD Workflow:**
- `/gtd-daily-start` - Morning planning with AI work review
- `/gtd-daily-end` - Evening wrap-up and AI delegation
- `/gtd-weekly-review` - Comprehensive weekly review
- `/gtd-monthly-strategic` - Monthly planning and goal setting
- `/today` - Generate daily briefing

## How It Works

```
1. Capture everything to inbox.org
           ↓
2. /gtd-daily-end processes inbox, delegates :AI: tasks
           ↓
3. AI agents execute overnight (research, content, analysis)
           ↓
4. /gtd-daily-start reviews completed AI work
           ↓
5. You focus on what matters
```

**AI Task Delegation:**
```org
* TODO Research market trends for Q1 :AI:research:
* TODO Draft blog post about productivity :AI:content:
* TODO Generate weekly metrics report :AI:data:
* TODO Track project milestones :AI:pm:
```

## Installation

```bash
# Clone to ~/Data
git clone https://github.com/datacore-one/datacore.git ~/Data
cd ~/Data

# Quick setup (copy templates)
cp CLAUDE.template.md CLAUDE.md
cp install.yaml.example install.yaml
```

**See [INSTALL.md](INSTALL.md) for complete installation guide**, including:
- Template activation for all files
- Adding team spaces
- Installing modules
- Configuring Emacs and Obsidian

## Documentation

Documentation is part of the knowledge base at `0-personal/notes/pages/datacore-docs/`:

- [Commands Reference](0-personal/notes/pages/datacore-docs/commands.md)
- [Agents Reference](0-personal/notes/pages/datacore-docs/agents.md)
- [Modules](0-personal/notes/pages/datacore-docs/modules.md)

Open with Obsidian for wiki-link navigation, or browse markdown directly.

## Requirements

- [Claude Code](https://claude.ai/code) - AI coding assistant
- [Emacs](https://www.gnu.org/software/emacs/) with org-mode (for GTD)
- [Obsidian](https://obsidian.md/) or [Logseq](https://logseq.com/) (for PKM)

## Philosophy

> *"Live long and prosper."*

Datacore is built on the belief that technology should serve human flourishing, not demand sacrifice. Your knowledge becomes leverage. Your expertise works while you rest. One person with Datacore has the capabilities of many.

**Core principles:**
- Augment, don't replace - AI assists, humans decide
- Knowledge is leverage - your expertise as an asset
- Single capture point - inbox.org, then route and clear
- Progressive processing - inbox to action to knowledge to archive

## Contributing

Datacore is open source and welcomes contributors. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE) for details.

---

*Datacore is built by Datacore. The AI system that bootstraps itself into existence.*

**[datacore.one](https://datacore.one)**
