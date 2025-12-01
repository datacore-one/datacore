# Today

Generate the daily briefing and append it to today's journal.

## Behavior

1. **Detect context**: Check if running from a space directory or root
2. **Generate briefing**: Create Today content with relevant sections
3. **Append to journal**: Add under `## Daily Briefing` heading in `notes/journals/YYYY-MM-DD.md`
4. **No user prompts**: Write directly without asking for permission

## Output Location

**Personal (root or 0-personal/):**
- Append to: `0-personal/notes/journals/YYYY-MM-DD.md`
- Add under heading: `## Daily Briefing`
- Create journal file if it doesn't exist (with standard frontmatter)

**Space (e.g., 1-datafund/):**
- Append to: `[space]/today/YYYY-MM-DD.md` (create if needed)
- Spaces use dedicated today/ directory for team visibility

## Personal Today Content

Generate under `## Daily Briefing` heading:

```markdown
## Daily Briefing

### Focus
[Suggested focus based on deadlines, energy patterns, calendar]

### Priority Tasks
[Top 3-5 tasks from org/next_actions.org, sorted by priority and due date]
- Include DEADLINE items for today
- Include SCHEDULED items for today
- Flag any OVERDUE items

### Calendar
[Today's calendar events if available]

### Overnight AI Work
[Summary of completed :AI: tasks from journal/org since last briefing]

### Needs Your Decision
[Items flagged for human review by agents]

### This Week
[Upcoming deadlines and scheduled reviews]

### Top 3 Must-Win Battles
[Distilled priorities for the day]
```

## Space Today Content

For team spaces, write to `[space]/today/YYYY-MM-DD.md`:

```markdown
# [Space] Today - [Date]

## Team Status
[Active members, anyone out]

## Today's Priorities
[From org/next_actions.org or GitHub Issues with priority labels]

## GitHub Activity (24h)
[Recent PRs, issues, comments via `gh` CLI]

## Standup Preview
[Draft standup from activity, ready to edit/send]

## Decisions Pending
[Items awaiting decision, sorted by age]

## This Week
[Key events for the week]
```

## Implementation Steps

1. Determine context (personal vs space)
2. Read org/next_actions.org for priorities (DEADLINE, SCHEDULED, PRIORITY A)
3. Scan recent journal entries for AI work completed overnight
4. Check for WAITING items needing follow-up
5. Identify decisions pending human input
6. Generate markdown content
7. **Write directly to file** (no user confirmation needed):
   - Personal: Append `## Today` section to journal file
   - Space: Write to today/YYYY-MM-DD.md
8. Display brief console summary

## Journal File Handling

If journal file doesn't exist, create with frontmatter:
```markdown
---
type: journal
date: YYYY-MM-DD
---

## Daily Briefing
[generated content]
```

If journal exists but has no `## Daily Briefing` section, append it.

If `## Daily Briefing` section exists, replace it with fresh content.

## Configuration

From `.datacore/config.yaml`:

```yaml
today:
  time: "06:00"  # Auto-generation time for cron
  include:
    - priorities
    - calendar
    - ai_work_summary
    - decisions_needed
```

## Cron Usage

```bash
# Personal briefing at 6 AM
0 6 * * * cd ~/Data && claude -p "/today"

# Space briefing at 7 AM
0 7 * * * cd ~/Data/1-datafund && claude -p "/today"
```

## Output

- Content written directly to journal (personal) or today/ file (space)
- Brief console summary of top priorities
- No downstream prompts or questions
