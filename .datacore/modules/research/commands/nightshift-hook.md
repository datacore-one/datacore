---
name: nightshift-hook
description: nightshift-hook command
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:nightshift-hook
  tags:
    - nightshift-hook
---

# Research Hook: Nightshift Integration

## Command Context

### When to Reference Research Module

**Always reference when:**
- Nightshift overnight pipeline is executing
- Research queue has pending TODO items in research_learning.org
- Need to process links and generate podcasts before morning briefing

**Key decisions this hook informs:**
- How many items to process this cycle (respects max_sources_per_night)
- Whether to generate podcasts (checks nlm availability)
- Which focus areas have the most pending items

### Quick Reference

| Question | Answer |
|----------|--------|
| When does this run? | Nightshift research timer (02:00 UTC) |
| What does it process? | TODO items from research_learning.org |
| Does it generate podcasts? | Yes, if nlm CLI is available and >= 3 sources processed |
| What's the processing limit? | max_sources_per_night setting (default 20) |
| What outputs are created? | Literature notes, zettels, podcasts, journal entry, action items |

### Agents This Hook Invokes

| Agent | Purpose |
|-------|---------|
| research-orchestrator | Full pipeline: discovery, extraction, synthesis, podcasts, post-processing |

### Integration Points

- **research_learning.org** - Source of TODO items to process
- **research-orchestrator agent** - Invoked in nightshift mode (non-interactive)
- **podcast-creator agent** - Spawned by orchestrator for podcast generation
- **0-personal/content/podcasts/** - Podcast output directory
- **Journal** - Session summary written to daily journal

---

This hook is invoked by nightshift for overnight research processing.

## Trigger

Called by nightshift scheduler via `nightshift run --command=/research-daily`.

## Mode

Non-interactive. No user confirmation needed — process all eligible TODO items.

## Workflow

### Step 1: Count and Prioritize Research Queue

Read research_learning.org and count pending items:

```python
import sys
from pathlib import Path

sys.path.insert(0, '.datacore/modules/research/lib')

data_root = Path.home() / "Data"
research_org = data_root / "0-personal" / "org" / "research_learning.org"

# Count TODO items
todo_count = 0
if research_org.exists():
    content = research_org.read_text()
    todo_count = content.count("** TODO") + content.count("*** TODO")
```

If todo_count == 0, log "Research queue empty — skipping" and exit.

### Step 2: Check nlm Availability

```bash
which nlm 2>/dev/null || echo "nlm not available — podcasts will be skipped"
```

### Step 3: Invoke Research Orchestrator

Spawn `research-orchestrator` agent with nightshift mode:

**Agent prompt:**
```
Process the research queue from research_learning.org in nightshift (non-interactive) mode.

Settings:
- Max sources: 20 (or max_sources_per_night from settings)
- Generate podcasts: yes (if nlm available and >= 3 sources)
- Extract action items: yes
- Update industry landscape: yes
- Write journal entry: yes

Processing order:
1. Prioritize items with [#A] priority
2. Then [#B] (default)
3. Then [#C]
4. Within each priority, process newest first

For each TODO item:
1. Fetch and analyze the URL via knowledge-extractor
2. Create literature note and zettels
3. Extract action items to next_actions.org
4. Mark as DONE with :OUTPUT: and :ZETTELS: properties

After all items processed:
1. Group processed items by topic
2. Generate research synthesis report
3. If >= 3 sources processed, spawn podcast-creator for a daily research podcast
4. Write processing summary to journal

Output the morning briefing section for /today.
```

### Step 4: Verify Outputs

After orchestrator completes, verify:
- Literature notes created in `0-personal/notes/2-knowledge/literature/`
- Processed items marked DONE in research_learning.org
- Podcast generated in `0-personal/content/podcasts/` (if applicable)
- Journal entry written

### Step 5: Report Results

Log summary for nightshift journal:

```
Research processing complete:
- Items processed: X/Y
- Literature notes: X
- Zettels: X
- Action items: X
- Podcast: [generated|skipped]
- Failures: X (details in journal)
```

## Error Handling

### No Items to Process
Log "Research queue empty" and exit cleanly (exit 0).

### URL Fetch Failures
- Keep failed items as TODO for next cycle
- Log failures but continue processing remaining items
- Never fail the entire batch for individual URL failures

### nlm Not Available
- Skip podcast generation
- Log "nlm unavailable — podcast skipped"
- All other processing continues normally

### Orchestrator Timeout
- Default timeout: 45 minutes (for 20 sources)
- If timeout, commit partial progress (items already marked DONE stay DONE)
- Log "Partial processing — X of Y items completed"

## Settings Reference

From `module.yaml` and `settings.local.yaml`:

```yaml
research:
  max_sources_per_night: 20
  podcast:
    auto_generate: true
  action_extraction:
    enabled: true
    max_per_source: 5
    default_priority: "B"
  post_processing:
    update_research_org: true
    update_journal: true
    update_industry_landscape: true
```
