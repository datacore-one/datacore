# Personal Space

This is the personal workspace within Datacore.

## Structure

```
0-personal/
├── org/              # Full GTD system
│   ├── inbox.org
│   ├── next_actions.org
│   ├── someday.org
│   └── habits.org
├── notes/            # Obsidian/Logseq PKM
│   ├── journals/     # Daily journal
│   ├── pages/        # Wiki pages
│   ├── Clippings/    # Web clippings
│   ├── 0-inbox/      # Note inbox
│   ├── 1-active/     # Active focus areas
│   ├── 2-knowledge/  # Permanent knowledge
│   └── 3-archive/    # Historical
├── code/             # Personal projects
└── content/          # Generated content
```

## Focus Areas (1-active/)

Define your focus areas here. Examples:
- **work/**: Day job projects and tasks
- **side-projects/**: Personal projects
- **learning/**: Skills development
- **health/**: Health tracking, fitness

## GTD Workflow

- **inbox.org**: Single capture point
- **next_actions.org**: Actionable tasks with :AI: tags for delegation
- AI agents scan for :AI: tagged tasks and execute overnight
- Morning briefing shows AI work completed

## Commands

Available commands depend on installed modules. Core commands:
- `/gtd-daily-start`, `/gtd-daily-end`
- `/gtd-weekly-review`, `/gtd-monthly-strategic`
- `/today`

## Agents

Available for :AI: task processing:
- **conversation-processor**: ChatGPT export → knowledge artifacts
- **research-link-processor**: URL batch processing for research
- **gtd-inbox-processor**: Inbox entry processing

See root `~/Data/CLAUDE.md` for full documentation.

## Parent Context

For full Datacore documentation, see `~/Data/CLAUDE.md`

---

**Setup**: Copy this file to `CLAUDE.md` and customize your focus areas.
