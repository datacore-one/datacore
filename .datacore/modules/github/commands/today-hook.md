---
name: today-hook
description: today-hook command
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:today-hook
  tags:
    - today-hook
---

# GitHub Hook: /today Integration

## Command Context

### When to Reference GitHub Module

**Always reference when:**
- /today command is invoked (automatic hook)
- User requests daily briefing or morning summary
- User asks about GitHub activity

**Key decisions the module informs:**
- Which items need user attention vs agent handling
- Whether nightshift ran and processed GitHub tasks
- What new activity happened across orgs

### Quick Reference

| Question | Answer |
|----------|--------|
| What format? | YOUR ITEMS (structured) + ORG ACTIVITY (table) |
| What items? | Past 24 hours of GitHub activity |
| When to run live scan? | If nightshift didn't run overnight |
| What tone? | Factual, concise, action-oriented |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| (None directly) | Reads scan cache or runs scanner scripts |

### Integration Points

- **/today command** — parent command that calls this hook
- **/triage-github** — can run full triage if nightshift missed
- **scan_cache.json** — cached scan results
- **next_actions.org** — where tasks are created

---

This hook adds GitHub activity summary to the daily briefing.

## Trigger

Called by `/today` command when github module is installed.

## Behavior

### Path 1: Nightshift Ran Overnight

Check if nightshift ran by looking for output files:

```python
from pathlib import Path
from datetime import date, timedelta
import sys

sys.path.insert(0, str(Path.home() / "Data" / ".datacore" / "lib"))
from triage_utils import check_nightshift_ran

data_dir = Path.home() / "Data"
nightshift_ran = check_nightshift_ran(data_dir)
```

If nightshift ran:

1. **Show agent report** — scan next_actions.org for recently completed :AI:github: tasks:
   - Tasks marked DONE today/yesterday with :AI:github: tag
   - Extract LOGBOOK entries for what the agent did
   - Show count + links

2. **Run fresh triage** — execute `/triage-github` to get current state:
   - New mentions, authored comments, org activity
   - Create new tasks for items not yet tracked

Output format:
```markdown
### GitHub

**Agent Report** (overnight):
- 2 issues auto-fixed: [PR #58](url), [PR #12](url)
- 1 proposal posted: [fairdrop#42](url)

**New Activity** (last 24h):
[/triage-github summary output here]
```

### Path 2: Nightshift Did NOT Run

If nightshift didn't run:

1. **Flag the issue**:
```markdown
> ⚠ Nightshift did not run overnight. Running GitHub triage live.
```

2. **Run `/triage-github` live** — full scan + task creation during /today

3. **Create tasks** — so nightshift picks them up next cycle

Output format:
```markdown
### GitHub

> ⚠ Nightshift did not run overnight. Running GitHub triage live.

[/triage-github full summary output here]
```

## Section to Generate

The hook produces a `### GitHub` section containing:

1. **Agent Report** (if nightshift ran) — what was auto-completed overnight
2. **Your Items** — mentions, comments on your issues, PR review requests
3. **Org Activity** — compact table of activity per org
4. **Tasks Created** — count of new :AI:github: tasks

## Conditions

| Condition | Behavior |
|-----------|----------|
| `gh` CLI not available | Skip section, show warning |
| No GitHub activity | Show "No GitHub activity in the last 24 hours" |
| Nightshift didn't run | Flag it, run triage live |
| Rate limited during /today | Use cached data from last scan |
| Scan cache exists from today | Use cache, don't re-scan |

## Tone

- Factual, action-oriented
- Lead with what needs attention
- Agent report first (what was done for you), then new items
