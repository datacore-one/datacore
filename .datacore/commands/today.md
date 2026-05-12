---
name: today
description: Your AI Chief of Staff's morning briefing — the main product deliverable of Data
recall:
  # Per DIP-0029, engrams matching these references are injected before this command runs.
  scopes:
    - command:today
  tags:
    - today
    - daily-briefing
  query:
    - "/today briefing structure"
    - "/today personalization"
---

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
| Output location? | Personal space journal: `0-personal/notes/journals/YYYY-MM-DD.md` |
| Nightshift outputs? | `*/0-inbox/nightshift-*.md` |
| Calendar source? | Google Calendar — multiple accounts (from `settings.local.yaml`) |
| Market phase? | Pre-computed by nightshift cron at 05:30 UTC |
| What DIPs govern this? | DIP-0009 (GTD), DIP-0011 (Nightshift) |

### Cron Schedule (nightshift server, UTC)

| Time | CEST | Job |
|------|------|-----|
| 05:30 | 07:30 | `/analyze-market-phase` — trading signals for briefing |
| 06:00 | 08:00 | `/today` — morning briefing generation |

---

## Step 1: Create Tracked Checklist

**Before doing anything else**, create a tracked task list for each step.

Use `TaskCreate` to create one task per step:

```
1. "Check for existing briefing"
2. "Sync repositories"
3. "Fetch Oura vitals"
4. "Fetch calendar events"
5. "Scan email"
6. "Scan GitHub"
7. "Collect nightshift results"
8. "Compute GTD health"
9. "Fetch news headlines"
10. "Gather trading data"
11. "Gather venture data"
12. "Check research outputs"
13. "Execute inline module hooks"
14. "Generate briefing"
15. "Write to journal"
16. "Generate standup drafts"
17. "Execute post-hooks (audio + notifications)"
18. "Verify completion"
```

---

## Step 2: Check for Existing Briefing

Check if today's briefing already exists:

```bash
python3 -c "
from pathlib import Path
from datetime import date
today = date.today().isoformat()
journal = Path.home() / 'Data' / '0-personal' / 'notes' / 'journals' / f'{today}.md'
if journal.exists():
    content = journal.read_text()
    if '## Daily Briefing' in content:
        print('EXISTS')
    else:
        print('NO_BRIEFING')
else:
    print('NO_FILE')
"
```

**If EXISTS (incremental mode):**
1. Pull repos (quick sync only — step 3)
2. Read and display the existing briefing
3. Check for new commits since the briefing was written
4. If new activity found, append `## Updates since briefing` section
5. Skip to step 16 (standup generation) — this is always interactive
6. Start interactive session

**If NO_BRIEFING or NO_FILE:**
Proceed with full briefing generation (step 3 onward).

---

## Step 3: Sync Repositories

Pull latest from all repos (brings nightshift outputs from server).

```
for each space in [root, 0-personal, 1-* through 7-*]:
  git stash && git pull --rebase && git stash pop
```

- `0-personal` syncs with nightshift server directly (not GitHub)
- Team spaces sync with GitHub
- If pull fails after 2 retries, warn and continue

---

## Step 4: Fetch Oura Vitals

Read token from `.datacore/env/oura.env` (var: `OURA_PERSONAL_ACCESS_TOKEN`),
fetch from Oura API v2:
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

---

## Step 5: Fetch Calendar Events

Query ALL configured Google Calendar accounts from `settings.local.yaml`.

```python
import yaml
from pathlib import Path

settings = yaml.safe_load((Path.home() / 'Data' / '.datacore' / 'settings.local.yaml').read_text())
accounts = settings['sync']['adapters']['calendar'].get('accounts', [])

# For each account, create adapter and fetch events
from sync.adapters.google_calendar import GoogleCalendarAdapter

all_events = []
for acct in accounts:
    adapter = GoogleCalendarAdapter(
        calendar_id=acct['calendar_id'],
        account=acct.get('name')
    )
    service = adapter._get_service()
    if service:
        # Fetch today + next 7 days, merge into all_events
        # Deduplicate by event ID (same event may appear on shared calendars)
```

Include attendee names, meeting duration, and flag prep-needed meetings.

**Event classification** — use Google Calendar `responseStatus` and metadata:

| responseStatus | Attendees | Recurring + self-created | Display as |
|----------------|-----------|--------------------------|------------|
| `accepted` | >0 | any | **Confirmed** — show in agenda timeline |
| `tentative` | >0 | any | **Tentative** — flag for decision |
| `needsAction` | >0 | any | **Unconfirmed** — highlight, needs RSVP |
| `declined` | >0 | any | Skip — don't show |
| `self-created` | 0 | yes | **Time block** — show as background context, not a meeting |
| `self-created` | 0 | no | **Personal** — show in agenda |

In the briefing agenda, prefix each meeting:
- `[C]` Confirmed → just show time + title + attendees
- `[?]` Unconfirmed → "RSVP needed" callout, include in Decisions Due
- `[T]` Tentative → mention but don't plan around it
- Time blocks → group separately: "Blocked: 17:00 Iva (recurring)"

---

## Step 6: Scan Email

Use the mail module scanner to get inbox summary:

```python
from mail.adapters.gmail import GmailAdapter
adapter = GmailAdapter({'address': GMAIL_ADDRESS})  # from settings.local.yaml
service = adapter._get_service()
```

**Goal: email inbox zero.** Classify and auto-archive noise.

| Category | Pattern | Action |
|----------|---------|--------|
| CI notifications | GitHub "Run failed/passed", own repos | Archive |
| Dependabot PRs | dependabot[bot] sender | Archive |
| Newsletters | beehiiv, substack, known senders | Archive, route interesting to research |
| Marketing/promo | Hotels, OKX, Swisscom, SaaS upsells | Archive |
| NPM publish | npm "Successfully published" | Archive |
| GA4 reports | noreply-analytics@google | Archive |
| Security advisories | GitHub security advisory | Route to GTD task |
| Accounting/payroll | Invoices, receipts, expenses | Keep — accounting |
| Team communication | Team member domains | Keep — actionable |
| External outreach | Unknown senders, pitches | Keep — needs decision |
| Calendar updates | Google Calendar invitations | Archive (in calendar) |
| Releases | GitHub Release notifications | Archive |

Report inline: "Email: 244 → 39 (205 archived)"

---

## Step 7: Scan GitHub

Use `gh` CLI across all spaces:

```bash
gh pr list --search "review-requested:@me" --json title,url,updatedAt 2>/dev/null
gh pr list --author @me --json title,url,state 2>/dev/null
gh api notifications --jq '.[] | select(.unread)' 2>/dev/null
```

---

## Step 8: Collect Nightshift Results

Check all spaces for overnight execution:
- DONE tasks with :NIGHTSHIFT_COMPLETED: property in past 24h
- REVIEW tasks needing attention
- Output files: `*/0-inbox/nightshift-*.md`
- Summarize with quality scores

### Bot & Automated Commit Activity

Show what bots and automated processes committed across all spaces in the past 24h.
This gives the user visibility into what changed while they weren't looking.

```bash
# For each space directory (root, 0-personal, 1-* through 8-*):
git -C [space] log --since="24 hours ago" --oneline --all 2>/dev/null
```

Group commits by actor (nightshift, plur, bots) and by space. Present as a compact
summary in the Spaces section, e.g.:

```
**Overnight commits**: nightshift: 12 (0-personal: 6, 6-meridian: 4, 3-fds: 2) ·
plur: 3 (5-plur: 2, 2-datacore: 1) · miles: 1 (8-firm)
```

Show the commit messages (abbreviated) so the user knows WHAT changed, not just counts.

---

## Step 9: Compute GTD Health

```python
# Inbox count, completed yesterday, total open, oldest open,
# overdue deadlines, scheduled for today
```

Use `org_workspace_adapter.py` — never grep raw .org files.

---

## Step 10: Fetch News Headlines

```bash
python3 .datacore/modules/news/lib/feed_fetcher.py  # if >4h stale
```

Read from `.datacore/modules/news/data/headlines.json`.

---

## Step 11: Gather Trading Data

If trading module installed:

```bash
python3 ~/.datacore/modules/trading/lib/gateio/today_summary.py --remote
```

Also read market phase analysis output (pre-computed by nightshift cron at 05:30 UTC).
Check `0-personal/0-inbox/` for market phase report. Integrate signals and
suggestions into the briefing's trading section.

**IMPORTANT — Educational tone for trading data:**
The user is learning trading. Raw monitoring jargon is NOT helpful. When presenting
trading data (especially BZZ anchor-drift, monitoring bands, bid-ask compression,
pattern density, etc.), always:
1. **Explain what each metric means** in plain English first
2. **Say what it means for the user's money** — is it good, bad, neutral?
3. **Give the "so what"** — does the user need to do anything?
4. Use analogies where helpful (e.g., "anchor-drift is how far BZZ price has drifted
   from your average buy price — like being underwater on a mortgage")
5. Keep raw numbers but wrap them in context: "Anchor-drift at -16.2% (your BZZ
   holdings are worth 16% less than what you paid — still in the caution zone but
   improving toward the -15% comfort threshold)"

Never output raw monitoring vocabulary without translation.

---

## Step 12: Gather Venture Data

If ventures module installed, read `venture.yaml` from each venture space.
Check cadence health, hypothesis status, budget.

---

## Step 13: Check Research Outputs

Look for completed research tasks and generated podcasts:
- `*/0-inbox/nightshift-*-research.md`
- Podcast files from NotebookLM pipeline

---

## Step 14: Execute Inline Module Hooks

Discover and execute all module hooks with `slot: inline` that contribute
content TO the briefing. Their output is woven into the appropriate section.

```python
from pathlib import Path
import yaml

modules_dir = Path.home() / "Data" / ".datacore" / "modules"
for module_yaml in modules_dir.glob("*/module.yaml"):
    with open(module_yaml) as f:
        manifest = yaml.safe_load(f)
    hook = (manifest.get("hooks") or {}).get("today")
    if hook:
        slot = hook.get("slot", "inline") if isinstance(hook, dict) else "inline"
        if slot == "inline":
            # Read hook instructions and execute
            # Output feeds into the briefing section matching the module's slot
```

**Currently registered inline hooks:**

| Module | Briefing section |
|--------|-----------------|
| metacognition | Knowledge base pulse — file counts, stubs, suggested command |

---

## Step 15: Generate Briefing

Compose the briefing sections in this order. **Every section is adjusted to the
capacity level from Oura.** The tone is a chief of staff speaking to their
principal — direct, personal, occasionally coaching.

### Briefing Structure

The goal is **inbox zero across all inboxes** (email, GitHub, GTD org).
Data processes everything proactively — archive noise, route to org/research,
create tasks. The briefing only shows what remains for the user.

There is NO dedicated "What Data Did" section. The work is visible throughout.

**Data is proactive by default.** Only suggest (don't act) when:
- Sending external communications (emails, messages)
- Financial decisions or transactions
- Deleting/archiving content the user created
- Anything irreversible affecting other people

```markdown
## Daily Briefing

## Good Morning
[Coach-like opening. Reference vitals, yesterday, what was handled overnight.]

## The World
[News synthesis + market prices + trading signals. Research outputs/podcasts.]

## Your Agenda
[What needs YOU today. Meetings + tasks + actionable emails + GitHub items.
Everything else already handled. Capacity-adjusted.]

## Spaces
[Per-space: work done + GitHub triage + priorities + venture status.]

## Decisions Due
[Formal decisions + email decisions needing human input.]

## Horizon
[This week + strategic priorities.]

## Proactive Suggestions
[Things Data can do tonight with approval. External comms = draft only.]

### Metacognition
[Knowledge base pulse from inline hook. File counts, stubs, suggested command.]

## Data's Observation
[Always last. Pattern analysis in Data's voice.]
```

### Section Details

#### Good Morning

3-5 sentences max. Reference vitals, yesterday, capacity nudge, coach element.

**Vitals detail block** (compact, below the narrative):
```
Recovery: 82 (Moderate) | Sleep: 86 | HRV: 73 | Steps yesterday: 12,721
Deep work: 4-5h | Workout: Normal | Meetings: 3-4 max
```

#### The World

Synthesized narrative, not bullet points:
1. Opening: Overall sentiment, major macro theme
2. Middle: Crypto, tech/AI developments, trading signals from market phase analysis
3. Closing: Key theme to watch today

**Tone:** Analytical, concise, professional. Like a Bloomberg terminal morning note.

#### Your Agenda

Merge meetings (from ALL calendar accounts), priority tasks, email items,
and GitHub items into one chronological + priority view.

**Capacity adjustment:**
- High (85+): 5-7 items
- Moderate (70-84): 3-5 items
- Low (55-69): 2-3 items
- Recovery (<55): 1-2 admin only

**GTD health** — compact inline:
```
GTD: 3 inbox | 236 open | 20 done yesterday | oldest: 461d (archive candidate)
```

#### Spaces

Per-space with narrative context (not commit hashes). GitHub triage integrated.
Group quiet spaces. Include venture status where applicable.

#### Horizon

This week (verify all day names via `datacore.date`), strategic priorities,
proactive suggestions for overnight work.

#### Data's Observation

Always last. Curious, analytical, no contractions, occasionally playful.
Pattern sources: productivity, habit streaks, task trends, readiness correlation.

---

## Step 16: Write to Journal

**Output location:** `0-personal/notes/journals/YYYY-MM-DD.md`

**The Daily Briefing ALWAYS goes at the top** (after frontmatter).

**If file doesn't exist:** Create with frontmatter + briefing.
**If file exists but no `## Daily Briefing`:** Insert after frontmatter.
**If `## Daily Briefing` exists:** Replace in-place with fresh content.

**After writing:** `open <journal_path>` to launch in default editor.

---

## Step 17: Generate Standup Drafts (Interactive — Never Skipped)

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

## Step 18: Execute Post-Hooks

Discover and execute all module hooks with `slot: post`. These run AFTER
the briefing is written.

For each module with `slot: post`:
1. Read the hook's `instructions` field or referenced command file
2. Execute the instructions
3. Log success/failure — don't block on errors

**Currently registered post-hooks:**

| Module | What it does |
|--------|-------------|
| voice-terminal | Write `_spoken.txt` → Kokoro TTS → Telegram voice message |
| whatsapp | Push briefing notification to mobile |

**Voice-terminal instructions:**
1. Write butler-style spoken summary to `{journal_dir}/{date}_spoken.txt`
   - Voice of "Data" — trusted chief of staff. 120-180 words.
   - Cover: health, top priorities, meetings, market highlight, closing thought.
   - End with "Your full report is on your desk" or variant.
2. Generate audio and send:
   ```bash
   python3 .datacore/modules/voice-terminal/lib/speak_brief.py {date} --telegram
   ```

---

## Step 19: Verify Completion

Check all steps completed. Print console summary of top 3 priorities.

---

## Module Hook System

Each installed module with a `hooks.today` entry contributes content.

**Slot mapping:**

| Module | Slot | What it provides |
|--------|------|-----------------|
| health | Good Morning | Oura vitals, capacity level |
| news | The World | Synthesized news narrative |
| trading | The World + Spaces | Market signals + bot status |
| mail | Agenda | Processed inbox, remaining items |
| github | Spaces | PRs, issues, security alerts |
| nightshift | Agenda + Spaces | Task results |
| research | The World + Agenda | Outputs, pending reviews |
| meetings | Agenda | Meeting prep, standup preview |
| crm | Agenda | Attendee context, follow-ups |
| ventures | Spaces | Venture portfolio status |
| analytics | Spaces | Website metrics |
| verity | Spaces | MCP server health |
| comms | Horizon | Content calendar items |
| metacognition | inline (after Observation) | Knowledge base pulse |
| voice-terminal | post | Spoken briefing → Telegram |
| whatsapp | post | Push notification |

**Future slots:**
- `family` → Good Morning + Agenda
- `personal-finance` → The World
- `coach` → Good Morning

---

## Configuration

From `.datacore/settings.local.yaml`:

```yaml
sync:
  adapters:
    calendar:
      enabled: true
      accounts:
        - name: default
          calendar_id: YOUR_PRIMARY_CALENDAR_ID
        - name: secondary
          calendar_id: YOUR_SECONDARY_CALENDAR_ID
      days_ahead: 14

coach:
  enabled: true
  morning_checkin: true

today:
  time: "06:00"  # UTC (08:00 CEST)
  include:
    - all
```

## Output

- Content written directly to journal (no user confirmation needed)
- Journal opened in default editor for review
- Brief console summary of top 3 priorities
- Audio briefing sent to Telegram
- No downstream prompts or questions (except standup review)
