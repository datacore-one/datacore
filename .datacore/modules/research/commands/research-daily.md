# /research-daily

## Command Context

### When to Reference Research Module

**Always reference when:**
- User wants immediate processing instead of waiting for nightshift
- Testing the research pipeline during development
- Processing urgent research that can't wait until overnight
- Need to clear research queue before end of day
- Troubleshooting research processing issues

**Key decisions this command informs:**
- Whether to process all items or specific section/batch
- Whether to generate podcasts immediately or skip for nightshift
- Whether to extract action items and update CRM during processing
- How to handle URL failures (retry, skip, mark cancelled)

### Quick Reference

| Question | Answer |
|----------|--------|
| What does it process? | TODO items from research_learning.org (all, by section, or limited batch) |
| Does it generate podcasts? | Configurable - can skip if nlm unavailable |
| What's the processing limit? | max_sources_per_night setting (default 20) |
| What outputs are created? | Literature notes, zettels, action items, journal updates, landscape entries |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| research-orchestrator | Orchestrates entire processing pipeline with user-specified scope and options |

### Integration Points

- **research-orchestrator agent** - Invoked with processing scope (all/section/limited)
- **research_learning.org** - Input source scanned for TODO items
- **User confirmation** - Interactive prompts for scope and settings
- **Processing summary** - Results presented with follow-up action suggestions
- **nlm availability** - Checks if podcast generation is possible
- **Module settings** - Respects action_extraction, post_processing, max_links settings

---

Manually trigger daily research processing outside of nightshift.

## When to Use

- You want to process research links immediately (not wait for nightshift)
- Testing the research pipeline
- Processing a specific batch of links

## Workflow

### Step 1: Check Research Queue

First, show current state:

```
Checking research_learning.org...

TODO items found: X
- Project Alpha: X items
- Organization: X items
- Datacore: X items
- Personal: X items

Ready to process?
```

### Step 2: Confirm Processing Scope

Ask user:

"What would you like to process?"

1. **All TODO items** - Process everything in queue
2. **Specific section** - Only one focus area (e.g., Project Alpha)
3. **Limited batch** - First N items only
4. **Cancel** - Don't process now

### Step 3: Configure Options

Based on settings, offer configuration:

```yaml
Processing options:
  generate_podcast: true    # Create NotebookLM podcast?
  extract_actions: true     # Extract action items?
  update_crm: true          # Run CRM entity extraction?
  update_landscape: true    # Update industry landscape?

Proceed with these settings? (y/n/customize)
```

### Step 4: Execute Processing

Invoke `research-orchestrator` agent with selected options:

```
Starting research processing...

[1/5] Fetching: Pantera Capital - Privacy Renaissance
      → Literature note created
      → 3 zettels extracted
      → 1 action item generated

[2/5] Fetching: Calimero Network...
      → Literature note created
      → 2 zettels extracted

... (progress for each item)

Processing complete!
```

### Step 5: Show Summary

```
═══════════════════════════════════════════════════
RESEARCH PROCESSING COMPLETE
═══════════════════════════════════════════════════

Links processed: 5
Literature notes: 5
Zettels created: 12
Action items: 3
CRM entities: 8
Industry entries: 6

Outputs:
- Literature notes: 0-personal/notes/2-knowledge/literature/articles/
- Zettels: 0-personal/notes/2-knowledge/zettel/
- Action items: Added to next_actions.org

Would you like to:
1. View the research briefing
2. Generate a podcast from these sources
3. Review action items created
```

## Error Handling

### No Items to Process
```
No TODO items found in research_learning.org.

To add research links:
1. Open 0-personal/org/research_learning.org
2. Add entries under appropriate section:

   *** TODO [#B] Article Title
       Link: https://example.com/article
```

### URL Fetch Failures
```
Failed to fetch 2 URLs:
- https://example.com/paywall (403 Forbidden)
- https://example.com/moved (404 Not Found)

These items have been kept as TODO for retry.
Successfully processed: 3/5 items

Would you like to:
1. Continue with successful items
2. Retry failed URLs
3. Mark failed as CANCELLED
```

### nlm Not Available
```
NotebookLM CLI (nlm) not found or not configured.

Podcasts will be skipped. Other processing will continue.

To enable podcasts:
1. Install nlm: go install github.com/tmc/nlm@latest
2. Configure in settings.local.yaml:
   research:
     nlm_path: "/path/to/nlm"
```

## Settings Reference

From `module.yaml`:

```yaml
research:
  daily_processing:
    enabled: true
    min_podcasts: 2
    max_sources_per_night: 20

  action_extraction:
    enabled: true
    max_per_source: 5
    default_priority: "B"

  post_processing:
    update_research_org: true
    update_journal: true
    update_industry_landscape: true
```

Override in `~/.datacore/settings.local.yaml`.

## Your Boundaries

**YOU CAN:**
- Read research_learning.org
- Invoke research-orchestrator agent
- Show progress and summaries
- Offer follow-up actions

**YOU CANNOT:**
- Process more than max_sources_per_night setting
- Skip confirmation without user consent
- Delete research entries (only mark DONE)

**YOU MUST:**
- Show queue status before processing
- Confirm processing scope with user
- Report all failures clearly
- Offer recovery options for failures
