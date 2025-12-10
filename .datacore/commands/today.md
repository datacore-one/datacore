# Today

Generate the daily briefing and append it to today's journal.

## Behavior

1. **Sync repositories**: Pull latest changes from all repos (includes nightshift outputs from server)
   ```
   SYNCING REPOS
   ─────────────
   Pulling latest changes...

   datacore (root).......... [OK]
   datafund-space........... [OK]  ← nightshift outputs appear here
   datacore-space........... [OK]  ← nightshift outputs appear here

   [If pull fails, retry twice. If still fails, warn and continue.]
   ```

   **Important**: Server nightshift executes overnight and commits results to team space repos.
   Pulling brings those outputs to your local machine for review in `/today`.

2. **Sync knowledge database** (DIP-0004): Update the database with any overnight changes
   ```bash
   python ~/.datacore/lib/datacore_sync.py sync --quiet
   ```
   ```
   SYNCING DATABASE
   ────────────────
   Indexing changes...
     Tasks: 234 (5 new)
     Sessions: 156
     Files: 847

   [If sync fails, warn and continue - briefing still works from files]
   ```

3. **Detect context**: Check if running from a space directory or root
4. **Generate briefing**: Create Today content with relevant sections
5. **Append to journal**: Add under `## Daily Briefing` heading in `notes/journals/YYYY-MM-DD.md`
6. **No user prompts**: Write directly without asking for permission

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

### Today's Meetings
[Today's calendar events - this section is REQUIRED if any meetings exist]

Format meetings chronologically with time, duration, and attendees:
```
09:00 - 09:30  Weekly standup (Datafund team)
11:00 - 12:00  Investor call with ABC Capital
14:00 - 14:30  1:1 with @tfius
16:00 - 17:00  Product review (Verity)
```

Include:
- Meeting time and duration
- Meeting title
- Key attendees or context in parentheses
- Flag any meetings requiring preparation: ⚠️ Prep needed

If no meetings today: "No meetings scheduled - deep work day! 🎯"

**To fetch events** (if calendar adapter enabled):
```python
from sync.adapters.google_calendar import GoogleCalendarAdapter
adapter = GoogleCalendarAdapter(calendar_id="gregor@datafund.io")
events = adapter.pull_events(days=1)
for e in events:
    end_time = e.end_time.strftime('%H:%M') if e.end_time else ""
    print(f"  {e.start_time.strftime('%H:%M')} - {end_time}  {e.title}")
```

Or via CLI:
```bash
PYTHONPATH=.datacore/lib python3 -c "
from sync.adapters.google_calendar import GoogleCalendarAdapter
adapter = GoogleCalendarAdapter(calendar_id='gregor@datafund.io')
for e in adapter.pull_events(days=1):
    print(f'{e.start_time.strftime(\"%H:%M\")} - {e.end_time.strftime(\"%H:%M\") if e.end_time else \"\"} {e.title}')
"
```

### Nightshift Results (DIP-0011)
[Summary of overnight task execution with quality scores]

**Format:**
```
NIGHTSHIFT RESULTS
──────────────────

📍 Local (0-personal)
Tasks: 1 completed
| Task | Score | Output |
|------|-------|--------|
| DIP-0010 sync test | 0.92 | [[nightshift-exec-2025-12-10-task.md]] |

📍 Server (1-datafund)
Tasks: 5 completed, 2 needs review
| Task | Score | Output |
|------|-------|--------|
| Research FineWeb | 0.88 | [[nightshift-001-research.md]] |
| Review Mode Network | 0.91 | [[nightshift-002-research.md]] |
| ... | ... | ... |

⚠ Needs Review (1-datafund)
| Task | Score | Reason |
|------|-------|--------|
| Swarm grant research | 0.65 | Low confidence on grant eligibility |

📍 Server (2-datacore)
No nightshift tasks executed.

──────────────────
Total: 6 completed | 2 review | Cost: ~$1.20
```

**Check for results:**
1. Look for DONE tasks with :NIGHTSHIFT_COMPLETED: property in past 24h
2. Look for REVIEW tasks needing attention
3. Read outputs from ALL spaces' inboxes:
   - `0-personal/0-inbox/nightshift-*.md` (local execution)
   - `1-datafund/0-inbox/nightshift-*.md` (server execution)
   - `2-datacore/0-inbox/nightshift-*.md` (server execution)
4. Summarize evaluator feedback for review items
5. Group results by space for clarity

**To find nightshift outputs from all spaces:**
```bash
# Find all nightshift outputs from past 24 hours
find ~/Data/*/0-inbox -name "nightshift-*.md" -mtime -1 2>/dev/null | while read f; do
  echo "=== $f ==="
  head -20 "$f"  # Read frontmatter for score/status
done
```

**If no nightshift ran**: "No nightshift execution overnight."

### Needs Your Decision
[Items flagged for human review by agents]

**Nightshift Review Items:**
Tasks that completed but need human review (score < 0.70 or high evaluator variance):
```
⚠ Blog post draft needs review
   Score: 0.68 (below threshold)
   CEO: 0.82 "Good message"
   Editor: 0.55 "Tone inconsistent with brand"
   Output: 1-datafund/0-inbox/nightshift-003-content.md
   [Review and provide feedback]
```

**Sync Conflicts (DIP-0010 Phase 2):**
If there are unresolved sync conflicts in the queue, list them:
```
Sync Conflicts Requiring Decision:
- github:owner/repo#42: state conflict (org: DONE, external: open)
- github:owner/repo#15: priority conflict (org: A, external: C)
```
Check via: `python .datacore/lib/sync/conflict.py --unresolved`

### This Week
[Upcoming deadlines and scheduled reviews]

### Top 3 Must-Win Battles
[Distilled priorities for the day]

### Yesterday's Wins
[Extract DONE items from yesterday's journal - celebrate accomplishments]

### Team Spaces Update
[Summary of activity across team spaces - show only if team spaces exist]

For each numbered space (1-datafund, 2-datacore, etc.):
- Recent commits (past 24h) with contributor names
- Open PRs requiring attention
- GitHub Issues activity (new, closed)
- Any blockers flagged in org/next_actions.org

**To gather team activity**:
```bash
# For each space directory
for space in [1-9]-*; do
  echo "=== $space ==="
  cd "$space"

  # Recent commits
  git log --oneline --since="24 hours ago" --format="%h %an: %s"

  # Open PRs (if gh available)
  gh pr list --state open 2>/dev/null || echo "No gh access"

  cd ..
done
```

Format example:
```
### Team Spaces Update

**1-datafund** (3 commits today)
- @tfius: Fix API endpoint validation (abc1234)
- @crt: Update documentation (def5678)
- PR #42: Awaiting review (2 days)

**2-datacore** (quiet - no commits today)
- No recent activity
```

### New Modules Available
[Check CATALOG.md roadmap for modules not yet installed]

**To detect new modules**:
1. Read `.datacore/CATALOG.md` for available modules
2. Compare with installed modules in `.datacore/modules/`
3. List any modules in catalog not yet installed

Format example:
```
### New Modules Available

📦 **research** - Academic research workflows (Status: Available)
   Install: `git clone https://github.com/datacore-one/datacore-research .datacore/modules/research`

📦 **finance** - Personal finance tracking (Status: Planned)
   Coming soon - watch CATALOG.md for updates
```

If no new modules: skip this section entirely.

### Data's Observation
[Playful insight from pattern analysis - written in Data's voice]

Examples:
- "Fascinating. Your productivity peaks between 9-11 AM. I recommend scheduling deep work during this window."
- "I observe you have completed 3 consecutive days of morning routines. The evidence suggests habit formation is progressing."
- "Curious. Your WAITING items tend to resolve on Thursdays. Perhaps scheduling follow-ups for Wednesday would be optimal."
- "Your research tasks consistently exceed estimated effort by 40%. Adjusting future estimates would improve planning accuracy."
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
3. **Sync calendar** (DIP-0010 Phase 3):
   - Check if calendar adapter is enabled in settings
   - If enabled, sync today's events from Google Calendar
   - Update calendar.org with latest events
4. Scan recent journal entries for AI work completed overnight
5. Check for WAITING items needing follow-up
6. Identify decisions pending human input
7. **Extract yesterday's wins** - Read yesterday's journal for DONE items
8. **Gather team spaces update** (personal context only):
   - List all numbered directories (1-*, 2-*, etc.)
   - For each space: recent commits, open PRs, issue activity
   - Flag any blockers from space org/next_actions.org
9. **Check for new modules**:
   - Read `.datacore/CATALOG.md` for available modules (Modules table + Roadmap)
   - List installed modules from `.datacore/modules/`
   - Report any catalog modules not yet installed
10. **Generate Data's observation** - Analyze patterns from past 7 days:
    - Productivity patterns (time of day, day of week)
    - Habit streaks (consecutive completions)
    - Task completion trends
    - Effort estimate accuracy
    - Write in Data's voice (curious, analytical, no contractions)
11. Generate markdown content
12. **Write directly to file** (no user confirmation needed):
    - Personal: Append `## Daily Briefing` section to journal file
    - Space: Write to today/YYYY-MM-DD.md
13. **Open journal for review**: `open <journal_path>` to launch in default editor
14. Display brief console summary

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
- Journal opened in default editor for review
- Brief console summary of top priorities
- No downstream prompts or questions
