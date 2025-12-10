# Welcome to Datacore

Your AI-powered second brain is ready. This guide walks you through your first day.

## What You've Installed

Datacore gives you:

1. **GTD System** - `org/` folder with org-mode files for task management
2. **Knowledge Base** - `notes/` folder for Obsidian/Logseq notes
3. **AI Agents** - Automated task execution overnight
4. **Slash Commands** - Quick workflows for daily routines

## Your First Day

### Morning: Set Up Your Focus Areas

Run the daily start command:

```
/gtd-daily-start
```

On first run, Claude will interview you to create your focus areas in `notes/1-active/`. These represent the key areas of your life:

- Work projects
- Personal development
- Health
- Family
- Hobbies

### During the Day: Capture Everything

Throw everything into `org/inbox.org`:

```org
* TODO Call dentist
* TODO Review quarterly report
* TODO Research vacation destinations
* Interesting article about AI: https://example.com/article
```

Don't organize yet. Just capture. The system will help you process later.

### Evening: Process and Delegate

Run the daily end command:

```
/gtd-daily-end
```

This will:
1. Process your inbox entries
2. Route items to the right places
3. Identify tasks AI can handle overnight
4. Tag tasks for delegation (`:AI:research:`, `:AI:content:`, etc.)

### Next Morning: Review AI Work

The next `/gtd-daily-start` shows you what AI completed overnight:
- Research summaries
- Draft content
- Data analysis
- Project status updates

## Key Concepts

### The Inbox is Sacred

`org/inbox.org` is your single capture point. Everything goes there first. Process it to zero regularly.

### AI Task Delegation

Tag tasks in `org/next_actions.org` to delegate:

| Tag | What AI Does |
|-----|--------------|
| `:AI:research:` | Fetch URLs, create summaries |
| `:AI:content:` | Draft blog posts, emails |
| `:AI:data:` | Generate reports, metrics |
| `:AI:pm:` | Track projects, flag blockers |

### Progressive Processing

Information flows through stages:

```
Inbox -> Triage -> Action/Knowledge -> Archive
```

Nothing gets lost. Everything finds its place.

## Next Steps

1. **[[GTD Workflow]]** - Deep dive into the GTD system
2. **[[Commands Reference]]** - All available commands
3. **[[Agents Reference]]** - How AI agents work
4. **[[Modules]]** - Extend with trading, research, etc.

## Getting Help

- Run `/diagnostic` to check system health
- Check the [[datacore-docs/README|Documentation Index]]
- Visit [datacore.one](https://datacore.one) for guides

---

*Welcome aboard. Your second brain awaits.*
