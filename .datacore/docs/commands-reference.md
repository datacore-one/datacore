# Commands Reference

Detailed command flows, timing, and automation setup.

> Quick reference: See root `CLAUDE.md` for command tables with "When to Use"

## Command Flow

```
SESSION FLOW (multiple per day)
───────────────────────────────
Start conversation with goal
    └── Work happens, insights emerge
        └── /wrap-up ─── captures continuations, learning, journal

DAY FLOW (once per day)
───────────────────────
/today (6 AM cron)
    └── /gtd-daily-start (9 AM) ─── reviews AI work, sets Top 3
        └── [Sessions throughout day with /wrap-up]
            └── /tomorrow (EOD) ─── AI delegation, priorities, preview

WEEKLY
──────
/gtd-weekly-review (Fri 4 PM) ─── includes all areas
    └── /weekly-trading-review ─── trading-specific

MONTHLY
───────
/gtd-monthly-strategic (Last Fri)
    └── /monthly-performance ─── trading deep dive
```

## Typical Daily Usage

| Event | Command | Purpose |
|-------|---------|---------|
| 6 AM | `/today` (cron) | Daily briefing with insights |
| 9 AM | `/gtd-daily-start` | Morning planning |
| After each session | `/wrap-up` | Capture continuations, learning |
| End of day | `/tomorrow` | AI delegation, priorities |

**Multiple sessions per day is normal.** Run `/wrap-up` before closing each terminal.

## Command Design Principles

1. **Automatable** - Can be triggered via cron
2. **Idempotent** - Safe to run multiple times
3. **Self-contained** - All context in the definition
4. **Composable** - Can be chained
5. **Observable** - Output goes to files (journals, inbox)

## Cron Setup

```bash
# Morning briefing at 6 AM
0 6 * * * cd ~/Data && claude -p "/today"

# Nightly AI task processing at 2 AM
0 2 * * * cd ~/Data && claude -p "Run ai-task-executor"

# Weekly review reminder (optional)
0 16 * * 5 cd ~/Data && claude -p "/gtd-weekly-review"
```

## Progressive Summarization (Literature Notes)

Used by `gtd-research-processor` when creating literature notes:

```markdown
## Layer 1: Key Points (30 sec read)
- Bullet points of main ideas

## Layer 2: Summary (2 min read)
- Condensed overview

## Layer 3: Detailed Notes
- Full notes with quotes
```

---

*See also: `.datacore/commands/` for individual command definitions*
