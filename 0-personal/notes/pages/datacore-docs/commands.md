---
title: Commands Reference
type: reference
created: 2025-01-01
---

# Commands Reference

Datacore provides slash commands for common workflows. Run these in Claude Code.

## System Commands

### /diagnostic

**Run a full system diagnostic.**

Verifies that all Datacore components are properly installed and configured. TNG-style status report.

```
/diagnostic
```

**Checks:**
- Directory structure integrity
- Required files present
- org-mode files valid
- Agents available
- Module status

**Use when:**
- First installation
- Something seems broken
- After major changes

---

### /today

**Generate daily briefing.**

Creates a summary of priorities, calendar, and pending items for today.

```
/today
```

**Output:**
- Priority tasks from next_actions.org
- Scheduled items for today
- Deadlines approaching
- AI work completed overnight
- Decisions needing attention

---

## GTD Commands

### /gtd-daily-start

**Morning planning routine.**

Run this first thing in the morning to prepare for the day.

```
/gtd-daily-start
```

**What it does:**
1. Reviews overnight AI work (completed tasks, drafts)
2. Shows today's calendar/scheduled items
3. Surfaces priority tasks
4. Checks for overdue items
5. Prompts for daily intention setting

**Duration:** 5-10 minutes

---

### /gtd-daily-end

**Evening wrap-up and AI delegation.**

Run this at the end of your workday.

```
/gtd-daily-end
```

**What it does:**
1. Displays inbox.org contents
2. Guides you through processing each item:
   - Classify (action, reference, someday, trash)
   - Route to appropriate location
   - Tag for AI if applicable
3. Identifies `:AI:` tasks for overnight processing
4. Returns inbox.org to clean state
5. Provides tomorrow preview

**Duration:** 10-15 minutes

**Critical:** Inbox should be empty after this command completes.

---

### /gtd-weekly-review

**Comprehensive GTD system maintenance.**

Run weekly (Sunday recommended) for full system review.

```
/gtd-weekly-review
```

**What it does:**
1. Processes any remaining inbox items
2. Reviews each project:
   - Is it still active?
   - What's the next action?
   - Any blockers?
3. Scans someday/maybe for items to activate
4. Reviews upcoming calendar (2 weeks)
5. Checks waiting-for items for follow-up
6. Cleans up completed items
7. Verifies system health

**Duration:** 30-60 minutes

**This is the most important GTD habit.**

---

### /gtd-monthly-strategic

**Monthly strategic planning.**

Run monthly for big-picture review.

```
/gtd-monthly-strategic
```

**What it does:**
1. Reviews monthly goals and outcomes
2. Assesses AI delegation effectiveness
3. Identifies patterns (what's working, what's not)
4. Plans next month's priorities
5. Updates long-term vision/goals
6. Suggests system improvements

**Duration:** 45-90 minutes

---

## Command Patterns

### Running Commands

In Claude Code, type the command with the slash:

```
/gtd-daily-start
```

Commands are markdown files in `.datacore/commands/`. Claude reads the instructions and executes the workflow.

### Command Arguments

Most commands don't require arguments. They operate on your current Datacore installation.

### Creating Custom Commands

Add markdown files to `.datacore/commands/`:

```markdown
# my-command

Description of what this command does.

## Steps

1. First step
2. Second step
3. Third step
```

Then run with `/my-command`.

---

## Module Commands

Optional modules add additional commands. See [Modules](modules.md) for details.

### Trading Module

If installed (`module-trading`):

| Command | Description |
|---------|-------------|
| `/start-trading` | Pre-market routine |
| `/validate-trade` | Check trade against rules |
| `/log-trade` | Record trade execution |
| `/close-trading` | End-of-day review |
| `/check-position-health` | Risk metrics check |
| `/analyze-market-phase` | Market regime analysis |
| `/market-briefing` | Quick market context |
| `/weekly-trading-review` | Weekly performance |
| `/monthly-performance` | Monthly analysis |
| `/journal-digest` | Extract trading insights |

---

## Troubleshooting

**Command not found:**
- Check that the file exists in `.datacore/commands/`
- Verify the filename matches (case-sensitive)
- Run `/diagnostic` to check system health

**Command fails:**
- Check the command's prerequisites
- Ensure required files exist (org files, etc.)
- Look for error messages in output

**Unexpected behavior:**
- Re-read the command documentation
- Check if files are in expected state
- Run `/diagnostic`

## See Also

- GTD Workflow
- [Agents Reference](agents.md)
- [Modules](modules.md)
