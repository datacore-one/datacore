# Research Hook: /today Integration

## Command Context

### When to Reference Research Module

**Always reference when:**
- User runs /today command and research module is installed
- Need to display research queue status in morning briefing
- Showing Readwise Reader pending items

**Key decisions this hook informs:**
- Are there research items to process?
- Are there Readwise items ready to import?
- When was the last podcast generated?

### Quick Reference

| Question | Answer |
|----------|--------|
| When does this run? | Every /today command execution |
| What does it display? | Queue count, Readwise pending, last podcast |
| Does it sync Readwise? | No - just checks count. Full sync via /research-status |
| What if no items? | Shows "Research queue empty" status |

### Integration Points

- **research_learning.org** - Counts pending TODO items
- **Readwise API** - Checks archived item count (if configured)
- **/research-status** - Suggested for full details and import
- **Podcast directory** - Shows last generated podcast date

---

This hook adds research status to the daily briefing.

## Trigger

Called by `/today` command when research module is installed.

## Output

Generate a concise research status section for the daily briefing.

### Step 1: Count Research Queue

```python
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, '.datacore/modules/research/lib')

data_root = Path.home() / "Data"
research_org = data_root / "0-personal" / "org" / "research_learning.org"

# Count TODO items in research_learning.org
todo_count = 0
if research_org.exists():
    content = research_org.read_text()
    todo_count = content.count("** TODO") + content.count("*** TODO")
```

### Step 2: Check Readwise (if configured)

```python
from adapters.readwise import ReadwiseAdapter
from sync_state import get_sync_stats

readwise_count = 0
readwise_status = "not configured"

adapter = ReadwiseAdapter(data_root)
if adapter.is_configured():
    try:
        # Quick check: just archived items
        docs = adapter.list_documents(location="archive")
        readwise_count = len(docs)
        readwise_status = f"{readwise_count} items ready"
    except Exception:
        readwise_status = "connection error"
```

### Step 3: Check Last Podcast

```python
podcast_dir = data_root / "0-personal" / "content" / "podcasts"
last_podcast = None
if podcast_dir.exists():
    podcasts = sorted(podcast_dir.glob("*.mp3"), key=lambda p: p.stat().st_mtime, reverse=True)
    if podcasts:
        mtime = datetime.fromtimestamp(podcasts[0].stat().st_mtime)
        days_ago = (datetime.now() - mtime).days
        last_podcast = f"{days_ago} days ago" if days_ago > 0 else "today"
```

### Step 4: Generate Output

Output format for /today briefing:

```markdown
### Research

| Metric | Value |
|--------|-------|
| Queue | {todo_count} pending |
| Readwise | {readwise_status} |
| Last podcast | {last_podcast or "none"} |

{If readwise_count > 0: "Run `/research-status` to import Readwise items."}
{If todo_count > 5: "Consider running `/research-daily` to process queue."}
```

## Example Output

When items are pending:

```markdown
### Research

| Metric | Value |
|--------|-------|
| Queue | 5 pending |
| Readwise | 12 items ready |
| Last podcast | 2 days ago |

Run `/research-status` to import Readwise items.
```

When queue is clear:

```markdown
### Research

| Metric | Value |
|--------|-------|
| Queue | 0 pending |
| Readwise | not configured |
| Last podcast | today |

Research queue is clear.
```

## Error Handling

- If research_learning.org doesn't exist: Show "Queue: not set up"
- If Readwise token invalid: Show "Readwise: auth error"
- If API times out: Show "Readwise: connection timeout"
