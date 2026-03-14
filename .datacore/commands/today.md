# Today

## Command Context

### When to Reference DIP-0009

**Always reference when:**
- Generating daily priorities
- Reviewing nightshift results
- Extracting calendar events
- Summarizing team activity

**Key decisions this DIP informs:**
- Priority task selection (DEADLINE, SCHEDULED, #A)
- Nightshift output review workflow
- Journal entry format

### Quick Reference

| Question | Answer |
|----------|--------|
| Output location? | `journal/{date}.md` (personal), `{space}/today/{date}.md` (team) |
| Nightshift outputs? | `*/0-inbox/nightshift-*.md` |
| Calendar source? | Google Calendar via DIP-0010 adapter |
| What DIPs govern this? | DIP-0009 (GTD), DIP-0011 (Nightshift), DIP-0004 (Datacortex) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `journal-coordinator` | Journal entry creation |
| `coach` | Morning emotional check-in (optional) |

### Integration Points

- **DIP-0009** - GTD daily workflow
- **DIP-0011** - Nightshift results aggregation
- **DIP-0004** - Database sync
- **Module hooks** - CRM, News, etc.

---

Generate the daily briefing and insert it at the top of today's journal (after frontmatter).

## Step 0: Create Tracked Checklist (MANDATORY FIRST STEP)

**Before doing anything else**, create a tracked task list for the /today phases. This prevents step skipping in long briefing generation.

Use `TaskCreate` to create one task per major phase:

```
Tasks to create (mark in_progress when starting, completed when done):

1. "Sync repos and database" (activeForm: "Syncing repos and database")
2. "Structural check and vitals" (activeForm: "Running structural checks")
3. "Calendar and context" (activeForm: "Fetching calendar and context")
4. "Generate briefing sections" (activeForm: "Generating briefing")
5. "Module hooks" (activeForm: "Running module hooks")
6. "Write to journal" (activeForm: "Writing journal entry")
7. "Verify all checklist tasks completed" (activeForm: "Verifying checklist completion")
```

**The final task (#7) is a gate:** Before marking it complete, run `TaskList` and verify every prior task shows `completed`. If any task is still `pending` or `in_progress`, go back and finish it.

**Why this exists:** /today has 16 sub-steps across multiple phases. Without tracking, later steps (module hooks, ideas pipeline, decision reviews) get silently skipped. Each phase is a tracked checkpoint.

## Behavior

1. **Sync repositories**: Pull latest changes from all repos (includes nightshift outputs from server)
   ```
   SYNCING REPOS
   ─────────────
   Pulling latest changes...

   datacore (root).......... [OK]
   0-personal............... [OK]  ← pulls from nightshift server (private)
   teamspace................ [OK]  ← nightshift outputs appear here
   projectspace............. [OK]  ← nightshift outputs appear here

   [If pull fails, retry twice. If still fails, warn and continue.]
   ```

   **Important**: Server nightshift executes overnight and commits results.
   - `0-personal`: Syncs with nightshift server directly (no GitHub)
   - Team spaces: Sync with GitHub repos
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

3. **Migration & Structural Check** (DIP-0015): Verify structure compliance
   ```bash
   # Check for DIP changes
   python ~/.datacore/lib/migration_detector.py

   # Quick structural check (all spaces)
   python ~/.datacore/lib/structural_integrity.py --quick
   ```
   ```
   STRUCTURAL CHECK
   ────────────────
   ✅ Structure definitions unchanged
   ✅ 0-personal: passed (2 warnings)
   ✅ 1-teamspace: passed (0 warnings)
   ✅ 2-projectspace: passed (1 warning)

   [If errors found, show in briefing under "System Notices"]
   [If DIP changed, alert user to run /structural-integrity report]
   ```

   **Migration Detection:**
   - Computes checksums of DIP-0015 and DIP-0017
   - If checksums changed since last check: alert user
   - Updates `.datacore/state/structure_version.yaml` with last check time

   **Quick Structural Check:**
   - Runs fast checks: folder structure, companions, inbox freshness
   - Skips slow checks: naming, LFS, wiki-links
   - Results included in briefing if issues found

   **To run manually:**
   ```python
   from migration_detector import MigrationDetector
   from structural_integrity import StructuralIntegrityChecker, check_all_spaces

   # Check for DIP changes
   detector = MigrationDetector(Path.home() / 'Data')
   alert = detector.check_for_updates()
   if alert:
       print(f"⚠️ {alert.message}")

   # Quick structural check
   results = check_all_spaces(Path.home() / 'Data', quick_mode=True)
   for r in results:
       status = "✅" if r.passed else "❌"
       print(f"{status} {r.space}: {len(r.warnings)} warnings")
   ```

4. **Sync vitals from Oura** (if health module installed):
   ```
   SYNCING VITALS
   ──────────────
   Fetching Oura data (readiness, sleep, activity)...

   Readiness: 89 (High Capacity)
   Sleep: 80 | HRV balance: 93
   Activity: 92 | Steps: 8324

   [If Oura not configured, skip silently]
   [If API error, warn and continue]
   ```

   **How to fetch:**
   Read the Oura personal access token from `.datacore/env/oura.env` (format: `OURA_PERSONAL_ACCESS_TOKEN=xxx`).
   Fetch from Oura API v2 using `urllib.request` (no pip dependencies):

   ```python
   import urllib.request, json
   from datetime import date, timedelta
   from pathlib import Path

   # Load token
   env_file = Path.home() / 'Data' / '.datacore' / 'env' / 'oura.env'
   token = None
   if env_file.exists():
       for line in env_file.read_text().splitlines():
           if line.startswith('OURA_PERSONAL_ACCESS_TOKEN='):
               token = line.split('=', 1)[1].strip()

   if token:
       today = date.today().isoformat()
       yesterday = (date.today() - timedelta(days=1)).isoformat()
       headers = {'Authorization': f'Bearer {token}'}

       # Readiness
       url = f'https://api.ouraring.com/v2/usercollection/daily_readiness?start_date={yesterday}&end_date={today}'
       req = urllib.request.Request(url, headers=headers)
       readiness_data = json.loads(urllib.request.urlopen(req, timeout=10).read())

       # Sleep
       url = f'https://api.ouraring.com/v2/usercollection/daily_sleep?start_date={yesterday}&end_date={today}'
       req = urllib.request.Request(url, headers=headers)
       sleep_data = json.loads(urllib.request.urlopen(req, timeout=10).read())

       # Activity
       url = f'https://api.ouraring.com/v2/usercollection/daily_activity?start_date={yesterday}&end_date={today}'
       req = urllib.request.Request(url, headers=headers)
       activity_data = json.loads(urllib.request.urlopen(req, timeout=10).read())
   ```

   Use today's record if available, otherwise fall back to yesterday's.

5. **Fetch calendar events** from Google Calendar:
   ```
   FETCHING CALENDAR
   ─────────────────
   Today: [N] meetings
   This week: [N] meetings

   [If no Google API packages, warn and continue]
   ```

   **How to fetch:**

   > **Note:** `{{CALENDAR_ID}}` should be configured via `settings.local.yaml` under
   > `calendar.google_calendar_id`. Example: `google_calendar_id: "user@example.com"`

   ```python
   PYTHONPATH = str(Path.home() / 'Data' / '.datacore' / 'lib')
   import sys; sys.path.insert(0, PYTHONPATH)
   from sync.adapters.google_calendar import GoogleCalendarAdapter

   adapter = GoogleCalendarAdapter(calendar_id='{{CALENDAR_ID}}')
   if adapter.is_configured():
       service = adapter._get_service()
       if service:
           from datetime import datetime, timedelta
           now = datetime.utcnow()

           # Today's events
           today_events = service.events().list(
               calendarId='{{CALENDAR_ID}}',
               timeMin=now.isoformat() + 'Z',
               timeMax=(now + timedelta(days=1)).isoformat() + 'Z',
               maxResults=50, singleEvents=True, orderBy='startTime'
           ).execute().get('items', [])

           # This week's events (for lookahead)
           week_events = service.events().list(
               calendarId='{{CALENDAR_ID}}',
               timeMin=now.isoformat() + 'Z',
               timeMax=(now + timedelta(days=7)).isoformat() + 'Z',
               maxResults=50, singleEvents=True, orderBy='startTime'
           ).execute().get('items', [])
   ```

   **Important**: The adapter uses `sys.path.insert(0, PYTHONPATH)` with the full absolute path to `.datacore/lib/`. The `settings` module does NOT exist -- read `settings.local.yaml` directly or hardcode the calendar_id from it.

6. **Detect context**: Check if running from a space directory or root
7. **Generate briefing**: Create Today content with relevant sections
8. **Append to journal**: Add under `## Daily Briefing` heading in `journal/YYYY-MM-DD.md`
9. **No user prompts**: Write directly without asking for permission

## Output Location

**Personal (root or 0-personal/):**
- Insert at TOP of: `0-personal/journal/YYYY-MM-DD.md` (after frontmatter)
- Add under heading: `## Daily Briefing`
- Create journal file if it doesn't exist (with standard frontmatter)
- If file already has content (e.g., late-night wrap-up session), push it below the briefing

**Space (e.g., 1-teamspace/):**
- Append to: `[space]/today/YYYY-MM-DD.md` (create if needed)
- Spaces use dedicated today/ directory for team visibility

## Personal Today Content

Generate under `## Daily Briefing` heading:

```markdown
## Daily Briefing

### System Notices
[Show only if migration alerts or structural errors found]

**Migration Alert** (if DIP checksums changed):
```
⚠️ **Structure Definitions Updated**
DIP-0015 modified since last check.
→ Run `/structural-integrity report` to verify compliance
```

**Structural Issues** (if errors or warnings found):
```
⚠️ **Structural Warnings** (3)
- 0-personal/0-inbox: 15 items older than 7 days
- 1-teamspace: Missing companion for pitch-deck.key
- 2-projectspace/0-inbox: 7 items older than 7 days
```

If no issues: omit this section entirely.

**To generate**:
```python
from migration_detector import MigrationDetector, format_briefing_alert
from structural_integrity import check_all_spaces, format_briefing_section

# Migration check
detector = MigrationDetector(Path.home() / 'Data')
alert = detector.check_for_updates()
if alert:
    print(format_briefing_alert(alert))

# Structural check
results = check_all_spaces(Path.home() / 'Data', quick_mode=True)
section = format_briefing_section(results)
if section != "✅ Structural integrity verified":
    print(section)
```

### Morning Check-in (Coach)
[Optional REBT coaching check-in - always skippable]

**Spawn the `coach` agent in morning mode:**

```
MORNING CHECK-IN
────────────────
How are you feeling this morning? (1-10, or Enter to skip)
> [user input]

[If provided:]
  [Brief response based on rating]
  [If low: offer to note for evening reflection]
  [If high: acknowledge and continue]

Set an intention for today? (brief, or Enter to skip)
> [user input]

────────────────
Disputation form (if needed):
→ https://docs.google.com/forms/d/e/1FAIpQLSf99Nbf8jFY91kzsgaqSZJ80Z7AHDC9wbPjGyDW76-LCgjIKA/viewform
```

**Behavior:**
- Keep brief - don't delay the day
- If rating 1-4: offer to explore briefly OR note for evening
- If rating 5-10: acknowledge and move on
- Always link to form but don't push
- Log to journal under `## Coaching` section

**Skip behavior:**
- User can always skip with Enter, "skip", or "no"
- Skipping is judgment-free
- If skipped, omit Coaching section from journal

**Configuration** (in `.datacore/settings.local.yaml`):
```yaml
coach:
  enabled: true
  morning_checkin: true
```

If `coach.morning_checkin: false`, skip this section entirely.

### Health & Readiness
[Oura vitals-based capacity assessment - ALWAYS include when health module is installed]

**This section drives the Focus and Priority Tasks sections.** Readiness determines suggested workload.

Display current vitals with capacity recommendation:

```markdown
### Health & Readiness

**Recovery:** [score] ([level])
[1-line interpretation and workload recommendation]

**Sleep:** [sleep_score] | [total_sleep contributor] | Efficiency [efficiency]
**HRV:** [hrv_balance] | Resting HR [resting_heart_rate]
**Yesterday's Activity:** [activity_score] | [steps] steps

**Today's Capacity:**
- Deep work: [hours]h recommended
- Workout: [intensity]
- Meeting tolerance: [tolerance]
```

**Readiness-to-Workload Mapping:**

| Score | Level | Deep Work | Workout | Meetings | Recommendation |
|-------|-------|-----------|---------|----------|----------------|
| 85+ | High Capacity | 6-8h | Full intensity | Unlimited | Tackle hardest tasks. Schedule ambitious work. |
| 70-84 | Moderate | 4-5h | Normal | 3-4 max | Standard day. Pace yourself. |
| 55-69 | Low Capacity | 2-3h | Light/mobility only | 1-2 max | Light work only. Admin tasks. More breaks. |
| <55 | Recovery | 1h max | Rest day | Reschedule if possible | Rest. Light admin only. Consider taking the day off. |

**The Focus section must reflect the capacity level.** If readiness is Low or Recovery, explicitly recommend reduced workload and flag that today is not the day for deep engineering or hard conversations.

**Oura API v2 data to display:**

From `daily_readiness`:
- `score` → Recovery score (main number)
- `contributors.hrv_balance` → HRV balance
- `contributors.resting_heart_rate` → Resting HR contributor
- `contributors.recovery_index` → Recovery index
- `contributors.sleep_balance` → Sleep balance

From `daily_sleep`:
- `score` → Sleep score
- `contributors.total_sleep` → Total sleep duration contributor
- `contributors.efficiency` → Sleep efficiency
- `contributors.deep_sleep` → Deep sleep quality
- `contributors.rem_sleep` → REM quality
- `contributors.restfulness` → Restfulness

From `daily_activity`:
- `score` → Activity score
- `steps` → Step count
- `active_calories` → Active calories

**Fetching:** See Behavior step 4 above for the API call pattern.

**Fallback:** If no Oura data available:
```markdown
### Health & Readiness

No vitals data available. Defaulting to moderate capacity.
Configure Oura in `.datacore/env/oura.env` to enable.
```

### Focus
[Suggested focus based on **readiness level**, deadlines, energy patterns, calendar]

**The Focus narrative MUST incorporate vitals:**
- High (85+): "Your body is primed for a big day. Front-load the hardest work."
- Moderate (70-84): "Normal capacity. Standard pacing."
- Low (55-69): "Low energy today. Protect your time. Move meetings if possible."
- Recovery (<55): "Recovery day. Light admin only. Your body needs rest."

### Priority Tasks
[Top 3-5 tasks from org/next_actions.org, sorted by priority and due date]
- Use `gtd.deadline_warnings` tool to identify overdue and upcoming deadline tasks
- Include SCHEDULED items for today
- Use `gtd.project_health` tool to flag stuck or stale projects needing attention
- **Adjust quantity to capacity**: High=5-7 tasks, Moderate=3-5, Low=2-3, Recovery=1-2 admin only

### GTD Health
[System health metrics for inbox, task age, and throughput]

**Purpose**: Quick pulse check on GTD system hygiene. Surfaces inbox buildup, stale tasks, and yesterday's throughput.

**Format:**
```markdown
### GTD Health

| Metric | Value |
|--------|-------|
| Inbox size | 7 items |
| Completed yesterday | 4 tasks |
| Total open tasks | 38 |
| Oldest open task | 42 days (*Review data pipeline architecture*) |
```

**How to generate:**
```python
from pathlib import Path
from datetime import datetime, timedelta
import re

data_root = Path.home() / 'Data'

# 1. Inbox size — count top-level headings in inbox.org
inbox_file = data_root / '0-personal' / 'org' / 'inbox.org'
inbox_count = 0
if inbox_file.exists():
    for line in inbox_file.read_text().splitlines():
        if re.match(r'^\*\* ', line):
            inbox_count += 1

# 2. Completed yesterday — DONE tasks with CLOSED timestamp from yesterday
na_file = data_root / '0-personal' / 'org' / 'next_actions.org'
yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
completed_yesterday = 0
if na_file.exists():
    content = na_file.read_text()
    # Match CLOSED timestamps from yesterday
    completed_yesterday = len(re.findall(
        rf'CLOSED:\s*\[{yesterday}', content
    ))

# 3. Total open tasks — count TODO/NEXT/WAITING headings
open_count = 0
if na_file.exists():
    for line in na_file.read_text().splitlines():
        if re.match(r'^\*+ (TODO|NEXT|WAITING) ', line):
            open_count += 1

# 4. Oldest open task — find earliest CREATED property among open tasks
oldest_age = 0
oldest_title = ''
if na_file.exists():
    blocks = re.split(r'(?=^\*+ (?:TODO|NEXT|WAITING) )', content, flags=re.MULTILINE)
    for block in blocks:
        title_match = re.match(r'^\*+ (?:TODO|NEXT|WAITING) (.+?)(?:\s+:[\w:]+:)?\s*$', block, re.MULTILINE)
        created_match = re.search(r':CREATED:\s*\[?(\d{4}-\d{2}-\d{2})', block)
        if title_match and created_match:
            created_date = datetime.strptime(created_match.group(1), '%Y-%m-%d')
            age = (datetime.now() - created_date).days
            if age > oldest_age:
                oldest_age = age
                oldest_title = title_match.group(1).strip()
```

**Thresholds for warnings:**
- Inbox > 10 items: flag as "Inbox needs processing"
- Oldest task > 30 days: flag as stale
- 0 tasks completed yesterday: flag as "No completions yesterday"

**If inbox is 0 and no warnings**: Show a clean "GTD system healthy" one-liner instead of the table.

### Today's Meetings
[Today's calendar events from Google Calendar - ALWAYS fetch from API]

**How to fetch** (use raw Google Calendar API via the adapter):
```python
import sys
sys.path.insert(0, str(Path.home() / 'Data' / '.datacore' / 'lib'))
from sync.adapters.google_calendar import GoogleCalendarAdapter
from datetime import datetime, timedelta

adapter = GoogleCalendarAdapter(calendar_id='{{CALENDAR_ID}}')
if adapter.is_configured():
    service = adapter._get_service()
    if service:
        now = datetime.utcnow()
        # Today's events
        today_result = service.events().list(
            calendarId='{{CALENDAR_ID}}',
            timeMin=now.isoformat() + 'Z',
            timeMax=(now + timedelta(days=1)).isoformat() + 'Z',
            maxResults=50, singleEvents=True, orderBy='startTime'
        ).execute()
        today_events = today_result.get('items', [])

        # Week lookahead
        week_result = service.events().list(
            calendarId='{{CALENDAR_ID}}',
            timeMin=(now + timedelta(days=1)).isoformat() + 'Z',
            timeMax=(now + timedelta(days=7)).isoformat() + 'Z',
            maxResults=50, singleEvents=True, orderBy='startTime'
        ).execute()
        week_events = week_result.get('items', [])
```

**Format today's meetings** chronologically:
```
09:00 - 09:30  Weekly standup (@crt, @tadej)
11:00 - 12:00  Investor call with ABC Capital
14:00 - 14:30  1:1 with @teammate
```

Include:
- Meeting time and duration
- Meeting title
- Key attendees (first names or orgs) in parentheses
- Flag any meetings requiring preparation: Prep needed

If no meetings today: "No meetings scheduled -- deep work day!"

**Always include a week lookahead** after today's meetings:

```markdown
**Coming up this week:**
- Mon 24: Daily (10:00), Weekly Exec (12:00), Indy Semantics WG (19:00)
- Tue 25: Iva (17:00)
- Wed 26: Daily (10:00), DataFund x Zigchain (13:00), Comms Weekly (14:00)
```

This helps with prep planning. Flag any meetings tomorrow that need preparation.

**Important implementation notes:**
- Do NOT use `from settings import load_config` -- that module doesn't exist
- The calendar_id is `{{CALENDAR_ID}}` (from `settings.local.yaml`)
- Use `sys.path.insert(0, ...)` with the FULL absolute path to `.datacore/lib/`
- The adapter's `pull_events()` method wraps results in OrgCalendarEntry -- for raw event data (attendees, etc.), use the service directly as shown above
- Parse event start/end from `event['start']['dateTime']` or `event['start']['date']` (all-day)

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

📍 Server (1-teamspace)
Tasks: 5 completed, 2 needs review
| Task | Score | Output |
|------|-------|--------|
| Research FineWeb | 0.88 | [[nightshift-001-research.md]] |
| Review Mode Network | 0.91 | [[nightshift-002-research.md]] |
| ... | ... | ... |

⚠ Needs Review (1-teamspace)
| Task | Score | Reason |
|------|-------|--------|
| Infrastructure grant research | 0.65 | Low confidence on grant eligibility |

📍 Server (2-projectspace)
No nightshift tasks executed.

──────────────────
Total: 6 completed | 2 review | Cost: ~$1.20
```

**Check for results:**
1. Look for DONE tasks with :NIGHTSHIFT_COMPLETED: property in past 24h
2. Look for REVIEW tasks needing attention
3. Read outputs from ALL spaces' inboxes:
   - `0-personal/0-inbox/nightshift-*.md` (local execution)
   - `1-teamspace/0-inbox/nightshift-*.md` (server execution)
   - `2-projectspace/0-inbox/nightshift-*.md` (server execution)
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

### Deferred Learning Review (DIP-0019)

If `learning.auto_defer_learning_review` is `true` in settings, engram candidates
from yesterday's `/wrap-up` session are surfaced here for review.

**Check for deferred candidates:**
1. Call `datacore.inject` with prompt "deferred learning review" to load pending engrams
2. If candidates exist, present them for approval/dismissal
3. Call `datacore.resolve` for each candidate with user's choice

**Format:**
```
LEARNING REVIEW
───────────────
3 engram candidates from yesterday:

1. [behavioral] "User prefers narrow tables for data display"
   → Approve / Dismiss / Edit

2. [procedural] "Always run tag_validator before committing tag changes"
   → Approve / Dismiss / Edit

3. [architectural] "Nightshift routing uses config-driven rules, not hardcoded"
   → Approve / Dismiss / Edit
```

**If no deferred candidates:** Skip this section silently.

### Needs Your Decision
[Items flagged for human review by agents]

**Nightshift Review Items:**
Tasks that completed but need human review (score < 0.70 or high evaluator variance):
```
⚠ Blog post draft needs review
   Score: 0.68 (below threshold)
   CEO: 0.82 "Good message"
   Editor: 0.55 "Tone inconsistent with brand"
   Output: 1-teamspace/0-inbox/nightshift-003-content.md
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

For each numbered space (1-teamspace, 2-projectspace, etc.):
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

**1-teamspace** (3 commits today)
- @dev1: Fix API endpoint validation (abc1234)
- @dev2: Update documentation (def5678)
- PR #42: Awaiting review (2 days)

**2-projectspace** (quiet - no commits today)
- No recent activity
```

### Ideas Pipeline
[Surface ideas approaching or ready for graduation from ideas.org]

**Purpose**: Ensure promising ideas don't stall in staging. Surface ideas that are ready to act on or nearing the graduation threshold.

**How to generate:**
1. Read `[space]/org/ideas.org` for each space
2. Parse idea headings with their STATUS property and TOTAL score
3. Surface:
   - Ideas with `STATUS=ready` that haven't been graduated yet
   - Ideas with `STATUS=promising` and `TOTAL >= 13` (near threshold)
   - Count of total ideas by status

**Format:**
```
### Ideas Pipeline
- 2 ideas READY for graduation
- 1 idea PROMISING (near threshold, TOTAL=14)
- 5 ideas in staging
```

**To generate:**
```python
from pathlib import Path
import re

def parse_ideas(space_path):
    ideas_file = space_path / 'org' / 'ideas.org'
    if not ideas_file.exists():
        return None

    content = ideas_file.read_text()
    counts = {'ready': 0, 'promising': 0, 'staging': 0, 'captured': 0, 'graduated': 0}
    near_threshold = []
    ready_ideas = []

    # Parse headings and their properties
    blocks = re.split(r'(?=^\*+ )', content, flags=re.MULTILINE)
    for block in blocks:
        status_match = re.search(r':STATUS:\s*(\w+)', block)
        total_match = re.search(r':TOTAL:\s*(\d+)', block)
        title_match = re.match(r'\*+ (?:TODO |NEXT |DONE )?(.+)', block)

        if status_match:
            status = status_match.group(1).lower()
            counts[status] = counts.get(status, 0) + 1
            total = int(total_match.group(1)) if total_match else 0
            title = title_match.group(1).strip() if title_match else 'Untitled'

            if status == 'ready':
                ready_ideas.append(title)
            elif status == 'promising' and total >= 13:
                near_threshold.append((title, total))

    return counts, ready_ideas, near_threshold
```

**If no ideas.org exists**: Skip this section entirely.

### Decisions Due for Review
[Surface decisions approaching or past their review date]

**Purpose**: Ensure time-bound decisions get reviewed on schedule. Prevent decisions from going stale without recorded outcomes.

**How to generate:**
1. Read `.datacore/state/decisions.yaml`
2. Parse decision entries with `review_date` and `outcome` fields
3. Surface:
   - Decisions where `review_date` is within 7 days of today
   - Decisions where `review_date` has passed and `outcome` is still null

**Format:**
```
### Decisions Due
- DEC-2026-0101-001: "Database choice for project X" (review due in 3 days)
- DEC-2025-1201-003: "API versioning strategy" (overdue, no outcome logged)
```

**To generate:**
```python
from pathlib import Path
from datetime import datetime, timedelta
import yaml

decisions_file = Path.home() / 'Data' / '.datacore' / 'state' / 'decisions.yaml'
if decisions_file.exists():
    decisions = yaml.safe_load(decisions_file.read_text()) or {}
    today = datetime.now().date()
    upcoming_window = today + timedelta(days=7)
    due_items = []

    for dec_id, dec in decisions.items():
        review_date = dec.get('review_date')
        outcome = dec.get('outcome')
        title = dec.get('title', dec_id)

        if review_date:
            if isinstance(review_date, str):
                review_date = datetime.strptime(review_date, '%Y-%m-%d').date()

            if review_date <= today and not outcome:
                days_overdue = (today - review_date).days
                due_items.append(f'- {dec_id}: "{title}" (overdue by {days_overdue} days, no outcome logged)')
            elif review_date <= upcoming_window and not outcome:
                days_until = (review_date - today).days
                due_items.append(f'- {dec_id}: "{title}" (review due in {days_until} days)')
```

**If no decisions.yaml exists or no decisions due**: Skip this section entirely.

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

### Module Hooks

For each installed module with a `today` hook, include its output section.

**To discover module hooks:**
1. List modules in `.datacore/modules/`
2. For each module, read `module.yaml`
3. If `hooks.today` exists, read the hook file and generate that section

**News Module** (if installed):
When News module is present with `hooks.today`, include a **synthesized narrative summary** (2-3 paragraphs) that reads like a morning analyst briefing:

```markdown
### News Summary

Markets are showing caution following the Fed's hawkish December meeting, with rate cut expectations for 2025 now reduced significantly. This is weighing on risk assets across the board.

In crypto, Bitcoin demand metrics are shrinking according to CryptoQuant—a potential bear market signal—though institutional flows remain positive with BlackRock's ETF hitting $25B in yearly inflows. Ethereum developers are pushing forward with the 'Glamsterdam' upgrade targeting MEV fairness.

Key theme today: risk-off sentiment as markets reprice Fed expectations.
```

**Structure:**
1. Opening paragraph: Overall sentiment, major macro theme
2. Middle paragraph: Crypto, tech, geopolitical developments
3. Closing: Key theme to watch

**To generate**: Load headlines from `.datacore/modules/news/data/headlines.json`, group by category, synthesize into narrative. If stale (>4h), fetch first:
```bash
python3 .datacore/modules/news/lib/feed_fetcher.py
```

**Tone**: Analytical, concise, professional—like a Bloomberg terminal summary.

**CRM Module** (if installed):
When CRM module is present with `hooks.today`, include:

```
### CRM

**Meeting Context**
For each meeting today, show attendee relationship status:
```
10:00 - Partnership Discussion
  Attendees:
  - [[John Smith]] (Acme Corp) - Active | Last: Dec 15
  - [[Jane Doe]] (Acme Corp) - Cooling | Last: Nov 28
```

**Follow-ups Due**
`:CRM:` tasks scheduled for today from next_actions.org:
```
- [ ] Email [[John Smith]] partnership proposal update
- [ ] Send deck to [[Investor X]]
```

**Attention Needed** (if `auto_scan_enabled: true`):
High-value dormant contacts (>30 days):
```
⚠ [[Partner Contact]] - Dormant 38 days (was active partnership)
```

**To generate CRM section:**
```bash
PYTHONPATH=.datacore/lib:.datacore/modules/crm/lib python3 -c "
from crm_cli import cmd_status
import argparse
args = argparse.Namespace(refresh=False, days=90)
cmd_status(args)
" 2>/dev/null | head -30
```

Or read from `.datacore/state/crm/contacts-index.yaml` if it exists.

### Knowledge Nugget
[Surface recently extracted knowledge for spaced repetition]

**Purpose**: Ensure recently extracted knowledge doesn't languish unread. Surface one item per day from recent extractions (past 30 days) that hasn't been applied yet.

**Format**:
```markdown
### Knowledge Nugget

📚 **From Infrastructure Roam Extract** (3 days ago)

**Effective Meetings Guide** - WDWBW Framework
> End each meeting item with explicit "Who Does What By When"
> Keep to 45-minute timebox. The best meeting is the one you don't have.

*Source: [[effective-meetings-guide]]*
*Application task: Scheduled for Monday*
```

**Selection Algorithm**:
1. Scan `3-knowledge/` for files modified in past 30 days
2. Check if file has associated application task in `org/inbox.org` or `org/next_actions.org`
3. Prioritize items with upcoming application tasks (contextually relevant)
4. If no upcoming tasks, surface oldest un-surfaced item
5. Track "last_surfaced" in `.datacore/state/knowledge-surfacing.yaml`

**Contextual Surfacing** (if calendar available):
- If meetings today → surface meetings guide
- If project kickoff → surface project canvas methodology
- If social media work → surface stoic social media guide
- If DevRel/ecosystem → surface DevRel frameworks

**To generate**:
```python
from pathlib import Path
import yaml
from datetime import datetime, timedelta

knowledge_root = Path.home() / 'Data/0-personal/3-knowledge'
state_file = Path.home() / 'Data/.datacore/state/knowledge-surfacing.yaml'

# Load surfacing state
state = yaml.safe_load(state_file.read_text()) if state_file.exists() else {}

# Find recent knowledge files (past 30 days)
recent_cutoff = datetime.now() - timedelta(days=30)
recent_files = []
for f in knowledge_root.rglob('*.md'):
    if f.stat().st_mtime > recent_cutoff.timestamp():
        recent_files.append(f)

# Select item not recently surfaced
for f in sorted(recent_files, key=lambda x: state.get(str(x), {}).get('last_surfaced', '1970-01-01')):
    # Extract excerpt and surface
    content = f.read_text()
    # ... generate nugget
    break

# Update state
state[str(selected_file)] = {'last_surfaced': datetime.now().isoformat()}
state_file.write_text(yaml.dump(state))
```

**If no recent knowledge**: Skip this section entirely.

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
2. **Sync vitals from Oura** (health module):
   - Read token from `.datacore/env/oura.env`
   - Fetch readiness, sleep, activity from Oura API v2
   - Use today's data, fall back to yesterday's if not available
   - Determine capacity level (High/Moderate/Low/Recovery)
3. **Fetch calendar from Google Calendar**:
   - Use adapter at `.datacore/lib/sync/adapters/google_calendar.py`
   - Calendar ID: `{{CALENDAR_ID}}` (from `settings.local.yaml`)
   - Fetch today's events AND next 7 days for week lookahead
   - Use raw service API for attendee data (not `pull_events()`)
4. Read org/next_actions.org for priorities (DEADLINE, SCHEDULED, PRIORITY A)
5. **Compute GTD Health metrics**: inbox count, completed yesterday, open tasks, oldest task age
6. **Adjust task recommendations to capacity** from step 2
7. Scan recent journal entries for AI work completed overnight
8. Check for WAITING items needing follow-up
9. Identify decisions pending human input
10. **Extract yesterday's wins** - Read yesterday's journal for DONE items
11. **Gather team spaces update** (personal context only):
    - List all numbered directories (1-*, 2-*, etc.)
    - For each space: recent commits, open PRs, issue activity
    - Flag any blockers from space org/next_actions.org
12. **Surface ideas pipeline** (if ideas.org exists):
    - Read `[space]/org/ideas.org`
    - Parse STATUS and TOTAL properties from idea headings
    - Surface ready ideas, near-threshold promising ideas, and status counts
13. **Check decisions due for review**:
    - Read `.datacore/state/decisions.yaml`
    - Surface decisions with review_date within 7 days or overdue with no outcome
14. **Check for new modules**:
    - Read `.datacore/CATALOG.md` for available modules (Modules table + Roadmap)
    - List installed modules from `.datacore/modules/`
    - Report any catalog modules not yet installed
15. **Check module hooks**:
    - For each module in `.datacore/modules/`, read `module.yaml`
    - If `hooks.today` exists, include that module's section
    - CRM module adds: Meeting Context, Follow-ups Due, Attention Needed
16. **Generate Data's observation** - Analyze patterns from past 7 days:
    - Productivity patterns (time of day, day of week)
    - Habit streaks (consecutive completions)
    - Task completion trends
    - Effort estimate accuracy
    - Write in Data's voice (curious, analytical, no contractions)
17. Generate markdown content
18. **Write directly to file** (no user confirmation needed):
    - Personal: Insert `## Daily Briefing` at TOP of journal (after frontmatter), pushing existing content down
    - Space: Write to today/YYYY-MM-DD.md
19. **Open journal for review**: `open <journal_path>` to launch in default editor
20. Display brief console summary

## Journal File Handling

**The Daily Briefing ALWAYS goes at the top of the journal file** (immediately after frontmatter). Late-night `/wrap-up` sessions may have already created the journal file with session entries before `/today` runs in the morning. The briefing must appear first so the user sees their day plan at the top, not buried below overnight session notes.

**If journal file doesn't exist**, create with frontmatter + briefing:
```markdown
---
type: journal
date: YYYY-MM-DD
---

## Daily Briefing
[generated content]
```

**If journal file exists but has no `## Daily Briefing` section**, insert it immediately after the frontmatter closing `---`, pushing all existing content (session entries, etc.) below:
```markdown
---
type: journal
date: YYYY-MM-DD
---

## Daily Briefing        ← INSERTED HERE
[generated content]

## Session 1: [topic]    ← existing content pushed down
[wrap-up from last night]
```

**If `## Daily Briefing` section already exists**, replace it in-place with fresh content (preserve its position at the top).

**Implementation:**
```python
# Parse frontmatter end position
lines = content.split('\n')
frontmatter_end = 0
in_frontmatter = False
for i, line in enumerate(lines):
    if line.strip() == '---':
        if not in_frontmatter:
            in_frontmatter = True
        else:
            frontmatter_end = i + 1
            break

# Insert briefing after frontmatter, before existing content
before = '\n'.join(lines[:frontmatter_end])
after = '\n'.join(lines[frontmatter_end:])
new_content = f"{before}\n\n## Daily Briefing\n\n{briefing_content}\n{after}"
```

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
0 7 * * * cd ~/Data/1-teamspace && claude -p "/today"
```

## Output

- Content written directly to journal (personal) or today/ file (space)
- Journal opened in default editor for review
- Brief console summary of top priorities
- No downstream prompts or questions
