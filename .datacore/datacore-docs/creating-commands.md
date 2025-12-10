# Creating Commands

Commands are user-triggered workflows invoked with `/command-name`.

## Command File Structure

```markdown
# Command Name

Brief description of what this command does.

## Behavior

1. Step one
2. Step two
3. Step three

## Output

Where and how output is generated.
```

Commands are simpler than agents - they're executed interactively with Claude Code.

## Basic Example

Create `.datacore/commands/standup.md`:

```markdown
# Standup

Generate a daily standup from recent activity.

## Behavior

1. Read today's journal from `0-personal/notes/journals/`
2. Read yesterday's journal for context
3. Extract:
   - What was completed yesterday
   - What's planned for today
   - Any blockers
4. Format as bullet points
5. Output to console

## Output Format

```
**Yesterday:**
- Completed task 1
- Completed task 2

**Today:**
- Planned task 1
- Planned task 2

**Blockers:**
- None / [blocker description]
```
```

Run with: `/standup`

## Command vs Agent

| Aspect | Command | Agent |
|--------|---------|-------|
| Trigger | User runs `/name` | Task tagged `:AI:tag:` |
| Interaction | Can prompt user | Runs autonomously |
| Output | Console + files | Files + JSON response |
| Use case | Interactive workflows | Background processing |

Use commands for things you want to do interactively.
Use agents for things that run unattended.

## Anatomy of a Command

### 1. Title and Description

```markdown
# Command Name

One-line description. This shows in command listings.
```

### 2. Behavior Section

Step-by-step instructions:

```markdown
## Behavior

1. **First step**: What to do first
2. **Second step**: What to do next
3. **Output**: How to present results
```

### 3. Input Handling

If command accepts arguments:

```markdown
## Arguments

- `$1` - First argument (e.g., date)
- `$2` - Second argument (optional)

## Examples

```
/report 2024-11    # Generate November report
/report            # Generate current month report
```
```

### 4. Output Specification

Where does output go?

```markdown
## Output

**Console:** Brief summary shown to user
**File:** Full content written to `path/to/output.md`
```

### 5. Context Awareness

Commands can behave differently based on location:

```markdown
## Context

**From root or 0-personal/:**
- Use personal org files
- Write to personal journal

**From space directory (1-*, 2-*):**
- Use space org files
- Write to space journal
```

## Real Example: Weekly Review

```markdown
# GTD Weekly Review

Comprehensive weekly review following GTD methodology.

## Behavior

1. **Check calendar**
   - Review past week's events
   - Preview next two weeks

2. **Process inboxes**
   - `org/inbox.org` - clear to zero
   - `0-inbox/` folder - process notes
   - Email inbox (remind user)

3. **Review lists**
   - `org/next_actions.org` - still relevant?
   - `org/someday.org` - anything to activate?
   - Waiting items - follow-up needed?

4. **Review projects**
   - All active projects have next actions?
   - Any projects to complete or drop?

5. **Get creative**
   - Capture new ideas
   - Review goals
   - Plan next week

## Output

Append to today's journal:

```markdown
## Weekly Review - [Date]

### Inbox Status
- org/inbox.org: [X items → 0]
- 0-inbox/: [X notes processed]

### Lists Reviewed
- Next actions: [X active]
- Waiting: [X items, Y need follow-up]
- Someday: [X items]

### Projects
- Active: [X projects]
- Completed this week: [list]
- New: [list]

### Next Week Focus
- [Priority 1]
- [Priority 2]
- [Priority 3]
```

## Prompts

During review, ask:
- "Any new projects to add?"
- "Anything to move to someday/maybe?"
- "What's your main focus for next week?"
```

## Automatable Commands

Commands should work both interactively and via cron:

```markdown
## Cron Usage

```bash
# Run weekly review every Sunday at 6 PM
0 18 * * 0 cd ~/Data && claude -p "/gtd-weekly-review"
```

## Non-Interactive Mode

When run via cron (no user input available):
- Skip prompts
- Use defaults
- Write output to file only
```

## Command Registration

Commands are available when placed in:

| Location | Scope |
|----------|-------|
| `.datacore/commands/` | All spaces |
| `0-personal/.datacore/commands/` | Personal only |
| `1-[space]/.datacore/commands/` | That space only |
| `.datacore/modules/[mod]/commands/` | When module installed |

## Command Resolution Order

When multiple commands have the same name:

1. Space custom (highest priority)
2. Space modules
3. Personal custom
4. Personal modules
5. Root custom
6. Root modules (lowest priority)

## Tips

### Keep Commands Focused

One command = one workflow. Don't combine unrelated actions.

```markdown
# Bad: Do Everything
Process inbox, review calendar, send standup, generate report...

# Good: Focused
Process inbox only. Other actions are separate commands.
```

### Handle Missing Data

Commands should work even when data is incomplete:

```markdown
## Behavior

1. Read journal entries from this week
2. If no entries found:
   - Note "No journal entries this week"
   - Continue with available data
3. Generate summary from what's available
```

### Provide Feedback

Tell users what's happening:

```markdown
## Output

Display progress:
```
Scanning journal entries...
Found 5 entries this week.
Generating summary...
Done. Summary written to reports/weekly.md
```
```

### Make Output Actionable

Commands should produce something useful:

```markdown
## Output

Don't just report status. Include:
- What needs attention
- Suggested next actions
- Links to relevant files
```

## Testing Commands

1. Create your command file
2. Run `/your-command` in Claude Code
3. Check the output
4. Iterate on instructions

## Example Commands by Type

### Report Generation

```markdown
# Monthly Metrics

Generate monthly performance metrics.

## Behavior

1. Read all journal entries for [month]
2. Extract metrics:
   - Tasks completed
   - Projects advanced
   - Habits tracked
3. Calculate trends vs previous month
4. Generate report with charts (markdown tables)

## Output

Write to: `0-personal/content/reports/YYYY-MM-metrics.md`
```

### Data Extraction

```markdown
# Export Tasks

Export tasks to a format for external tools.

## Arguments

- `$1` - Format: `csv`, `json`, or `todoist`

## Behavior

1. Read `org/next_actions.org`
2. Parse all TODO items
3. Convert to requested format
4. Output to console (pipe-friendly)

## Examples

```
/export-tasks csv > tasks.csv
/export-tasks json | jq '.[] | .title'
```
```

### Integration

```markdown
# Sync GitHub

Sync org tasks with GitHub issues.

## Behavior

1. Read `org/next_actions.org`
2. For each item tagged `:github:`
   - Check if GitHub issue exists
   - Create if missing
   - Update status if changed
3. Pull new GitHub issues into org
4. Report sync summary

## Output

Console summary of sync actions.
```

## Next Steps

- [Creating Agents](creating-agents.md) - Autonomous processors
- [Modules](modules.md) - Packaging for distribution
- [Commands Reference](commands.md) - All built-in commands
