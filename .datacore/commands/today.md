# Today

Your AI Chief of Staff's morning briefing. The main product deliverable of Data.

## Command Context

### Philosophy

This is not a dashboard. It is a personal briefing from someone who knows you —
your energy, your patterns, your priorities, your life. The tone is a trusted
chief of staff who also coaches: direct, warm, occasionally challenging.

The briefing tells a story in three acts:
1. **The World** — what happened while you slept (news, markets, overnight work)
2. **Your Day** — what needs you today (agenda, email, GitHub, decisions)
3. **Your Horizon** — what is coming and what matters (week ahead, strategic view)

### Design Principles

- **Personalization over information** — know the user, don't just report data
- **Coach, don't report** — gentle nudges, not bullet lists. "Your body says moderate today" not "Readiness: 72"
- **Module slots, not hardcoded sections** — modules fill slots in the narrative flow
- **News first** — set world context before personal agenda
- **Done-first** — show what Data accomplished overnight before asking for decisions
- **Capacity-aware** — every recommendation adjusted to Oura readiness
- **Future-extensible** — family, personal finance, health goals will plug in naturally

### Quick Reference

| Question | Answer |
|----------|--------|
| Output location? | `0-personal/notes/journals/YYYY-MM-DD.md` |
| Nightshift outputs? | `*/0-inbox/nightshift-*.md` |
| Calendar source? | Google Calendar (from `settings.local.yaml` → `sync.adapters.calendar.calendar_id`) |
| What DIPs govern this? | DIP-0009 (GTD), DIP-0011 (Nightshift) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `journal-coordinator` | Journal entry creation |
| `coach` | Morning emotional check-in (optional) |

---

## Step 0: Create Tracked Checklist (MANDATORY FIRST STEP)

**Before doing anything else**, create a tracked task list for the /today phases.

Use `TaskCreate` to create one task per major phase:

```
1. "Sync and gather data" (activeForm: "Syncing repos, vitals, calendar, email, GitHub")
2. "Generate briefing" (activeForm: "Generating morning briefing")
3. "Write to journal" (activeForm: "Writing journal entry")
4. "Verify completion" (activeForm: "Verifying all steps done")
```

---

## Step 0b: Check for Existing Briefing (Incremental Mode)

Before running the full pipeline, check if today's briefing already exists:

```bash
python3 -c "
from pathlib import Path
from datetime import date
today = date.today().isoformat()
journal = Path.home() / 'Data' / '0-personal' / 'notes' / 'journals' / f'{today}.md'
if journal.exists():
    content = journal.read_text()
    if '## Daily Briefing' in content or '## Today' in content:
        print('EXISTS')
    else:
        print('NO_BRIEFING')
else:
    print('NO_FILE')
"
```

**If EXISTS (incremental mode):**
1. Pull repos (quick sync only)
2. Read and display the existing briefing
3. Check for new commits since the briefing was written
4. If new activity found, append `## Updates since briefing` section
5. Skip to step 11-bis (standup generation) — this is always interactive
6. Start interactive session

**If NO_BRIEFING or NO_FILE:**
Proceed with full briefing generation (steps 1 onward).

---

## Step 1: Sync and Gather Data

All data gathering happens first. Run these in parallel where possible.

### 1a. Sync repositories

Pull latest from all repos (brings nightshift outputs from server).

```
for each space in [root, 0-personal, 1-* through 7-*]:
  git stash && git pull --rebase && git stash pop
```

- `0-personal` syncs with nightshift server directly (not GitHub)
- Team spaces sync with GitHub
- If pull fails after 2 retries, warn and continue

### 1b. Fetch Oura vitals

Read token from `.datacore/env/oura.env`, fetch from Oura API v2:
- `daily_readiness` → score, contributors (hrv_balance, resting_heart_rate, recovery_index, sleep_balance)
- `daily_sleep` → score, contributors (total_sleep, efficiency, deep_sleep, rem_sleep, restfulness)
- `daily_activity` → score, steps, active_calories

Use today's record; fall back to yesterday's.

**Readiness-to-capacity mapping:**

| Score | Level | Deep Work | Workout | Meetings |
|-------|-------|-----------|---------|----------|
| 85+ | High | 6-8h | Full | Unlimited |
| 70-84 | Moderate | 4-5h | Normal | 3-4 max |
| 55-69 | Low | 2-3h | Light only | 1-2 max |
| <55 | Recovery | 1h max | Rest | Reschedule |

### 1c. Fetch calendar events

Use Google Calendar adapter (`.datacore/lib/sync/adapters/google_calendar.py`).
Calendar ID: from `settings.local.yaml` → `sync.adapters.calendar.calendar_id`.

```python
import sys
sys.path.insert(0, str(Path.home() / 'Data' / '.datacore' / 'lib'))
from sync.adapters.google_calendar import GoogleCalendarAdapter

adapter = GoogleCalendarAdapter(calendar_id=CALENDAR_ID)
service = adapter._get_service()
# Fetch today's events AND next 7 days
```

Include attendee names, meeting duration, and flag prep-needed meetings.

### 1d. Scan email

Use the mail module scanner to get inbox summary:

```python
sys.path.insert(0, str(Path.home() / 'Data' / '.datacore' / 'modules' / 'mail' / 'adapters'))
from gmail import GmailAdapter

adapter = GmailAdapter({'address': GMAIL_ADDRESS})  # from settings.local.yaml
service = adapter._get_service()
# Get inbox count, recent messages (past 24h), flagged/important
```

Classify into: NEEDS ATTENTION (urgent/important), INFORMATIONAL (FYI), AUTO-PROCESSED (by nightshift).

### 1e. Scan GitHub

Use `gh` CLI across all spaces:

```bash
# PRs needing review
gh pr list --search "review-requested:@me" --json title,url,updatedAt 2>/dev/null

# PRs I authored
gh pr list --author @me --json title,url,state 2>/dev/null

# Recent issues activity (past 24h)
gh api notifications --jq '.[] | select(.unread)' 2>/dev/null
```

### 1f. Collect nightshift results

Check all spaces for overnight execution:
- DONE tasks with :NIGHTSHIFT_COMPLETED: property in past 24h
- REVIEW tasks needing attention
- Output files: `*/0-inbox/nightshift-*.md`
- Summarize with quality scores

### 1g. Compute GTD health

```python
# Inbox count (** headings in inbox.org)
# Completed yesterday (CLOSED timestamps from yesterday)
# Total open tasks (TODO/NEXT/WAITING headings)
# Oldest open task (earliest CREATED property)
# Overdue deadlines
# Scheduled for today
```

### 1h. Fetch news headlines

```bash
python3 .datacore/modules/news/lib/feed_fetcher.py  # if >4h stale
```

Read from `.datacore/modules/news/data/headlines.json`.

### 1i. Gather trading data (if trading module installed)

```bash
python3 ~/.datacore/modules/trading/lib/gateio/today_summary.py --remote
```

### 1j. Gather venture data (if ventures module installed)

Read `venture.yaml` from each venture space. Check cadence health, hypothesis status, budget.

### 1k. Check research outputs

Look for completed research tasks and generated podcasts:
- `*/0-inbox/nightshift-*-research.md`
- Podcast files from NotebookLM pipeline
- Readwise Reader pending items

---

## Step 2: Generate Briefing

Compose the briefing sections in this order. **Every section is adjusted to the
capacity level from Oura.** The tone is a chief of staff speaking to their
principal — direct, personal, occasionally coaching.

### Briefing Structure

```markdown
## Daily Briefing

### Good Morning
[Coach-like opening. 2-3 sentences. Reference vitals, yesterday's wins,
set the tone for the day. This is the "human" moment.]

### The World
[News synthesis — what happened overnight. 2-3 narrative paragraphs.
Macro → crypto → tech/AI. Bloomberg analyst tone.
Include trading-relevant signals if trading module active.]

### What Data Did Overnight
[Everything accomplished while you slept. Nightshift results,
email auto-processing, research completed, podcasts generated.
This is the "chief of staff report" — proving value.]

### Your Agenda
[Chronological timeline merging meetings + priority tasks.
Capacity-adjusted. Includes email items needing attention,
GitHub PRs to review, and decisions pending.]

### Spaces
[Team activity with context. Not just commit hashes —
what happened, what matters, what needs you.
GitHub Issues/PRs requiring attention per space.]

### Horizon
[This week's deadlines and upcoming events.
Strategic priorities — what matters beyond today.
Decisions due for review.]

### Data's Observation
[Always last. Playful pattern analysis in Data's voice.
No contractions. Curious and analytical.]
```

---

### Section Details

#### Good Morning

A warm, personalized opening. Reference:
- Vitals: "Your body recovered well — readiness 85, deep sleep was excellent."
- Yesterday: "Yesterday was a 20-completion day. Don't try to repeat that."
- Capacity nudge: "Moderate capacity today. Front-load the hard thing."
- Coach element: If low readiness, gently recommend rest. If high, encourage ambition.

If `coach.morning_checkin: true` in settings, include:
```
How are you feeling this morning? (1-10, or Enter to skip)
```

Keep this to 3-5 sentences max. Set the emotional tone for the day.

**Vitals detail block** (compact, below the narrative):
```
Recovery: 82 (Moderate) | Sleep: 86 | HRV: 73 | Steps yesterday: 12,721
Deep work: 4-5h | Workout: Normal | Meetings: 3-4 max
```

#### The World

Synthesized narrative, not bullet points. Structure:
1. Opening: Overall sentiment, major macro theme
2. Middle: Crypto, tech/AI developments
3. Closing: Key theme to watch today

**Trading signals** (if trading module installed):
Weave in market-relevant data — SOL price action, BTC trend, volume signals.
Don't create a separate trading section; integrate into the world narrative.

**Source:** `.datacore/modules/news/data/headlines.json` grouped by category.
If stale (>4h), fetch first: `python3 .datacore/modules/news/lib/feed_fetcher.py`

**Tone:** Analytical, concise, professional. Like a Bloomberg terminal morning note.

#### What Data Did Overnight

Show everything accomplished autonomously. This section proves the value of
the AI Chief of Staff. Format:

```markdown
### What Data Did Overnight

**Nightshift** (N tasks completed, M need review)
- Research: [topic] — [quality score] — [[link to output]]
- Content: [draft] — [quality score] — [[link]]

**Email** (N processed, M need your attention)
- Auto-archived: 15 newsletters, 8 notifications
- Drafts prepared: 2 replies ready for review
- Flagged urgent: 1 from [sender] re: [subject]

**Research & Podcasts**
- Daily news podcast ready: [[link]] (12 min)
- Deep-dive: [topic] — [[link]] (18 min)
- Readwise: 5 items pending import

**GitHub**
- CI: all green across repos
- Auto-merged: dependabot PRs in [repo]
```

If nothing ran overnight: "Quiet night — no nightshift tasks were queued.
Consider adding :AI: tasks before bed for overnight execution."

#### Your Agenda

**This is the core actionable section.** Merge meetings, priority tasks,
email items, and GitHub items into one chronological + priority view.

Format as a timeline when meetings exist:
```markdown
### Your Agenda

**Morning**
- 09:00–09:30 Weekly standup (@crt, @tadej) — Prep: share DIP-0017 status
- [#A] Continue: Datacore infrastructure — central git origin (scheduled today)
- Reply to [sender] re: [subject] (flagged urgent)

**Afternoon**
- 14:00–14:30 1:1 with @teammate
- Review PR #42 in 2-datacore (2 days waiting)
- [#A] QVAC PoC — 3 days overdue, ship or renegotiate

**Decisions Needed**
- Nightshift output: blog post draft (score 0.68) — review and approve/reject
- Sync conflict: github:owner/repo#42 state mismatch

**Inbox** (3 items — process or delegate)
```

When no meetings: organize by priority blocks (Must-do / Should-do / Could-do),
still capacity-adjusted.

**Capacity adjustment:**
- High (85+): 5-7 items, ambitious scope
- Moderate (70-84): 3-5 items, standard
- Low (55-69): 2-3 items, protect time
- Recovery (<55): 1-2 admin only, suggest day off

**GTD health** — compact inline, not a separate section:
```
GTD: 3 inbox | 236 open | 20 done yesterday | oldest: 461d (archive candidate)
```

#### Spaces

For each numbered space with activity in the past 24h:

```markdown
### Spaces

**1-datafund** — 5 commits, 1 PR open
Crt backfilled journal entries (Jan-Apr). New highlevel intro deck for
government meetings (7 slides, locked). PR #15 needs review: API docs update.

**2-datacore** — 4 commits
Data platform + services pages shipped. "AI Chief of Staff" landing page
preview live. DIP-0017 archive processed.

**5-plur** — quiet
Sync only. No active work.
```

Include for each space:
- Recent commits with **context** (not just hashes)
- Open PRs requiring attention
- GitHub Issues activity
- Blockers from `org/next_actions.org`
- What's the space's current priority/focus

Skip quiet spaces or group them: "3-fds, 4-forge, 6-meridian, 7-megaphone: DIP-0017 routing only."

#### Horizon

```markdown
### Horizon

**This Week**
- Fri Apr 18: Monitor venture heartbeat, Reddit repost
- Sun Apr 20: Forge variants + A/B check-in, cap table continuation
- Tue Apr 22: Thomas Fundneider follow-up, Dubai RWA pilot

**Strategic Priorities**
Three things that matter beyond today:
1. QVAC PoC — overdue, blocks partnership momentum
2. Nightshift research pipeline — stalled, needs tasks queued
3. Calendar + email integration — now working, wire into nightshift

**Decisions Due**
- DEC-2026-0314-003: Late.dev API (overdue 3 days, no outcome)
- 3 architectural decisions approaching May 22 review
```

#### Data's Observation

Always the last section. Written in Data's voice — curious, analytical,
no contractions, occasionally playful.

Pattern sources (past 7 days):
- Productivity patterns (time of day, day of week)
- Habit streaks (consecutive completions)
- Task completion trends
- Effort estimate accuracy
- Readiness-to-output correlation

Examples:
- "Fascinating. Your productivity peaks between 9-11 AM. I recommend scheduling deep work during this window."
- "I observe you have completed 3 consecutive days of morning routines. The evidence suggests habit formation is progressing."
- "Your research tasks consistently exceed estimated effort by 40%. Adjusting future estimates would improve planning accuracy."

---

## Step 11-bis: Generate Standup Drafts (Interactive — Never Skipped)

For each team space where the user is a contributor:

1. Run carryover sync:
   ```bash
   python3 .datacore/lib/standup_sync.py carryover \
     --space [space_path] \
     --contributor [user]
   ```
2. Read yesterday's team journal `## @{contributor}` sections for new accomplishments
3. Generate draft standup per space
4. Present to user for review and approval
5. On approval: write `## Standup` to today's journal, update yesterday's checkboxes, sync org tasks
6. On skip: proceed without posting standups

---

## Step 3: Write to Journal

**Output location:** `0-personal/notes/journals/YYYY-MM-DD.md`

**The Daily Briefing ALWAYS goes at the top** (after frontmatter). Late-night
`/wrap-up` sessions may have created the file already — push existing content
below the briefing.

**If file doesn't exist:** Create with frontmatter + briefing.
**If file exists but no `## Daily Briefing`:** Insert after frontmatter.
**If `## Daily Briefing` exists:** Replace in-place with fresh content.

```python
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

before = '\n'.join(lines[:frontmatter_end])
after = '\n'.join(lines[frontmatter_end:])
new_content = f"{before}\n\n## Daily Briefing\n\n{briefing_content}\n{after}"
```

**After writing:** `open <journal_path>` to launch in default editor.

---

## Module Hook System

Each installed module with a `hooks.today` entry contributes content.
The briefing orchestrator reads `module.yaml` for each module and includes
hook output in the appropriate section slot.

**Slot mapping** — modules map to briefing sections:

| Module | Slot | What it provides |
|--------|------|-----------------|
| health | Good Morning | Oura vitals, capacity level |
| news | The World | Synthesized news narrative |
| trading | The World (prices) + Spaces/Meridian (positions, bots) | Market signals woven into news; detailed bot/position status under 6-meridian |
| nightshift | What Data Did | Overnight task results |
| mail | What Data Did + Agenda | Email triage, flagged items |
| github | What Data Did + Agenda | PRs, issues, CI status |
| research | What Data Did | Completed research, podcasts |
| meetings | Agenda | Meeting prep, standup preview |
| crm | Agenda | Attendee context, follow-ups due |
| ventures | Spaces | Venture portfolio status |
| analytics | Spaces | Website metrics summary |
| verity | Spaces | MCP server health |
| comms | Horizon | Content calendar items due |
| whatsapp | (notification) | Push briefing to mobile |

**Future slots:**
- `family` → Good Morning + Agenda (family calendar, kids activities)
- `personal-finance` → The World (portfolio, crypto holdings)
- `coach` → Good Morning (REBT check-in, intention setting)

---

## Configuration

From `.datacore/settings.local.yaml`:

```yaml
sync:
  adapters:
    calendar:
      enabled: true
      calendar_id: YOUR_CALENDAR_ID  # set in settings.local.yaml
      days_ahead: 14

coach:
  enabled: true
  morning_checkin: true

today:
  time: "06:00"
  include:
    - all  # or list specific sections
```

## Cron Usage

```bash
# Personal briefing at 6 AM
0 6 * * * cd ~/Data && claude -p "/today"
```

## Output

- Content written directly to journal (no user confirmation needed)
- Journal opened in default editor for review
- Brief console summary of top 3 priorities
- No downstream prompts or questions
