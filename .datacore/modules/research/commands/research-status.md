---
name: research-status
description: research-status command
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:research-status
  tags:
    - research-status
---

# /research-status

## Command Context

### When to Reference Research Module

**Always reference when:**
- Checking morning research pipeline health
- Before triggering manual processing with /research-daily
- Reviewing overnight nightshift results
- Monitoring knowledge base growth trends
- Diagnosing research processing issues

**Key decisions this command informs:**
- Whether to process pending items immediately or wait for nightshift
- Which focus areas need more research attention
- If URL failures need manual intervention
- Whether podcast generation is working correctly

### Quick Reference

| Question | Answer |
|----------|--------|
| What does this show? | Queue counts, Readwise items, recent podcasts, knowledge base growth |
| What files does it read? | research_learning.org, podcasts/, literature/, zettel/, industry-landscape.yaml |
| Does it modify anything? | Can import Readwise items to research_learning.org if user confirms |
| What follow-up actions? | Import Readwise, /research-daily, retry failed URLs |

### Agents This Command Invokes

None - this is a read-only status command that scans files and presents summaries.

### Integration Points

- **research_learning.org** - Scans for TODO/DONE counts per section
- **Readwise Reader API** - Checks for archived items ready to import
- **Podcast directory** - Lists recent audio files with metadata
- **Knowledge directories** - Counts literature notes and zettels
- **industry-landscape.yaml** - Counts landscape entries
- **/research-daily** - Suggested as follow-up action for pending items
- **Nightshift status** - May reference /nightshift-status for diagnostics

---

View research queue, recent podcasts, and processing statistics.

## When to Use

- Morning check on research pipeline status
- Before triggering manual processing
- Reviewing what was processed overnight
- Checking industry landscape growth

## Workflow

### Step 1: Gather Status

Scan the following sources:
- `research_learning.org` - Count TODO/DONE items per section
- `Readwise Reader API` - Check for archived items (if configured)
- `0-personal/content/podcasts/` - List recent podcasts
- `0-personal/notes/2-knowledge/literature/` - Count literature notes
- `0-personal/notes/2-knowledge/zettel/` - Count zettels
- `industry-landscape.yaml` - Count entries

### Step 1b: Check Readwise Reader

```python
import sys
from pathlib import Path

sys.path.insert(0, '.datacore/modules/research/lib')

from adapters.readwise import ReadwiseAdapter
from sync_state import get_imported_ids, get_sync_stats

data_root = Path.home() / "Data"
adapter = ReadwiseAdapter(data_root)
readwise_items = []
readwise_status = "not configured"

if adapter.is_configured():
    try:
        # Get archived documents (finished reading)
        all_docs = adapter.list_documents(location="archive")
        imported_ids = get_imported_ids(data_root)

        # Filter out already imported
        readwise_items = [d for d in all_docs if d.id not in imported_ids]
        readwise_status = f"{len(readwise_items)} ready to import"

        stats = get_sync_stats(data_root)
        if stats["last_sync"]:
            readwise_status += f" (last sync: {stats['last_sync'][:10]})"
    except Exception as e:
        readwise_status = f"error: {str(e)[:30]}"
```

### Step 2: Present Overview

Show a comprehensive status dashboard:

```
═══════════════════════════════════════════════════
RESEARCH STATUS
═══════════════════════════════════════════════════

📋 Research Queue (research_learning.org)
─────────────────────────────────────────────────
Section             Pending  Processed  Ready
─────────────────────────────────────────────────
Project Alpha           12         45      8
Datacore                 5         23      5
Organization             8         31      6
Trading                  3         12      2
Health & Longevity       7         18      7
Personal                 4         15      4
─────────────────────────────────────────────────
Total                   39        144     32

📖 Readwise Reader
─────────────────────────────────────────────────
Status: 12 items ready to import
Last sync: 2025-12-15
Categories: 8 articles, 3 PDFs, 1 epub

🎙️ Recent Podcasts (Last 7 Days)
─────────────────────────────────────────────────
Date        Title                          Duration
─────────────────────────────────────────────────
2025-12-18  Daily Research                 28:45
2025-12-18  Project Alpha Competitive Analysis    24:30
2025-12-17  Daily Research                 31:20
─────────────────────────────────────────────────
Total: 3 podcasts, 1h 24m

📚 Knowledge Base Growth (Last 30 Days)
─────────────────────────────────────────────────
Literature notes created:     142
Zettels generated:             58
Action items extracted:        18
Industry landscape entries:    34
CRM entities extracted:        26

⚠️  Issues
─────────────────────────────────────────────────
Failed URL fetches: 3 (marked for retry)
Items needing review: 2
```

### Step 3: Offer Follow-up Actions

Based on status, suggest next steps:

```
What would you like to do?

1. Import Readwise items to queue (12 available)
2. Process pending items now (/research-daily)
3. View failed URLs and retry
4. Review items marked needs_review
5. View industry landscape details
```

### Step 4: Readwise Import (if selected)

When user chooses to import Readwise items:

**Preview items:**
```
═══════════════════════════════════════════════════
READWISE IMPORT PREVIEW
═══════════════════════════════════════════════════

12 items to import from Readwise Reader:

Articles (8):
  1. "AI Is Transforming the Nature of the Firm" - HBR
  2. "The Future of Digital Identity" - a][ blog
  3. "Solid Protocol Deep Dive" - solidproject.org
  ...

PDFs (3):
  4. "Organization Whitepaper v2.pdf"
  5. "MPC Research Summary.pdf"
  ...

EPUBs (1):
  6. "Read Write Own" by Chris Dixon

Import all 12 items to research queue? [Y/n]
```

**On confirmation, import to research_learning.org:**

```python
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '.datacore/modules/research/lib')

from adapters.readwise import ReadwiseAdapter
from sync_state import add_imported_ids, set_last_sync, set_last_import_count

data_root = Path.home() / "Data"
research_org = data_root / "0-personal" / "org" / "research_learning.org"
today = datetime.now().strftime("%Y-%m-%d %a")

# Build org entries for each item
entries = []
for doc in readwise_items:
    # Map Readwise tags to org tags
    tags = ":readwise:"
    for rw_tag in doc.tags:
        mapped = tag_mapping.get(rw_tag.lower(), rw_tag.lower())
        tags += f"{mapped}:"

    # Get highlights if configured
    highlights_text = ""
    if include_highlights and doc.id:
        highlights = adapter.get_document_highlights(doc.id)
        if highlights:
            highlights_text = "\n*Highlights:*\n"
            for h in highlights[:5]:
                highlights_text += f"- {h['text'][:200]}...\n"

    entry = f"""
** TODO [#B] {doc.title} {tags}
:PROPERTIES:
:CREATED: [{today}]
:READWISE_ID: {doc.id}
:Link: {doc.url}
:AUTHOR: {doc.author}
:CATEGORY: {doc.category}
:END:

{doc.summary}
{highlights_text}
"""
    entries.append(entry)

# Append to research_learning.org
with open(research_org, 'a') as f:
    f.write("\n".join(entries))

# Update sync state
add_imported_ids([doc.id for doc in readwise_items], data_root)
set_last_sync(data_root=data_root)
set_last_import_count(len(readwise_items), data_root)
```

**Show confirmation:**
```
═══════════════════════════════════════════════════
READWISE IMPORT COMPLETE
═══════════════════════════════════════════════════

Imported: 12 items to research_learning.org

  Articles: 8
  PDFs: 3
  EPUBs: 1

Next steps:
- Items will be processed by nightshift overnight
- Or run /research-daily to process now

Sync timestamp updated.
```

## Error Handling

### research_learning.org Not Found
```
⚠️  research_learning.org not found at expected location.

Expected: 0-personal/org/research_learning.org

To create:
1. Create the file with section headings
2. Add research links as TODO items

Would you like me to create a template?
```

### No Podcasts Found
```
📭 No podcasts found in the last 7 days.

Possible reasons:
- Nightshift hasn't run yet
- nlm CLI not configured
- No research items were pending

Check nightshift status with /nightshift-status
```

### industry-landscape.yaml Missing
```
⚠️  Industry landscape file not found.

This file is created automatically during research processing.
Run /research-daily to process some links first.
```

## Settings Reference

Related settings in `~/.datacore/settings.local.yaml`:

```yaml
research:
  podcast_output_dir: "0-personal/content/podcasts"
  literature_output_dir: "0-personal/notes/2-knowledge/literature"
  zettel_output_dir: "0-personal/notes/2-knowledge/zettel"
  industry_landscape_file: "1-datafund/1-tracks/research/Industry landscape.md"
  research_org_file: "0-personal/org/research_learning.org"

  # Readwise Reader integration
  readwise:
    enabled: true
    api_token_env: ".datacore/env/readwise.env"
    locations: ["archive"]           # finished reading
    categories: ["article", "pdf", "epub"]
    skip_kindle: true
    include_highlights: true
    tag_mapping:
      team: "team"
      project-alpha: "project-alpha"
      trading: "trading"
```

**Readwise Setup:**
1. Get API token from https://readwise.io/access_token
2. Create `.datacore/env/readwise.env`:
   ```
   READWISE_ACCESS_TOKEN=your_token_here
   ```

## Your Boundaries

**YOU CAN:**
- Read research_learning.org and count entries
- List files in output directories
- Read industry-landscape.yaml
- Present formatted status report
- Suggest follow-up actions
- Query Readwise Reader API for pending items
- Import Readwise items to research_learning.org (with user confirmation)
- Update sync state after import

**YOU CANNOT:**
- Process research items (use /research-daily)
- Delete or archive entries from research_learning.org
- Delete items from Readwise
- Modify existing research_learning.org entries

**YOU MUST:**
- Show current counts accurately
- Report any missing files clearly
- Offer relevant follow-up actions
- Use consistent formatting
- Always ask for confirmation before importing
- Track imported IDs to avoid duplicates
