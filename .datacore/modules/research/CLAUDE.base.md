# Research Module

Automated research processing with NotebookLM podcast generation.

## Overview

This module processes `research_learning.org` daily during nightshift to:
- Fetch and analyze research URLs
- Generate NotebookLM podcasts (daily news + topical)
- Build industry landscape database
- Create literature notes and atomic zettels
- Produce morning research briefings

## Commands

| Command | Description |
|---------|-------------|
| `/research-status` | View research queue, recent podcasts, processing stats |
| `/create-podcast` | Create ad-hoc podcast from URLs |
| `/research-daily` | Manually trigger daily processing |

## Agents

| Agent | Model | Purpose |
|-------|-------|---------|
| `daily-research-processor` | Sonnet | Nightshift orchestrator - coordinates all sub-agents |
| `gtd-research-processor` | Sonnet | URL analyzer - creates literature notes and zettels |
| `action-item-extractor` | Haiku | Extracts actionable tasks from research outputs |
| `research-post-processor` | Haiku | Updates org files, journal, and industry landscape |
| `nlm-podcast-creator` | Sonnet | NotebookLM integration - creates podcasts from sources |

## Workflow

```
research_learning.org (TODO items with URLs)
        │
        ▼
daily-research-processor (orchestrator)
        │
        ├──► gtd-research-processor (per URL)
        │         │
        │         └──► Literature notes + Zettels
        │
        ├──► action-item-extractor (per literature note)
        │         │
        │         └──► next_actions.org tasks
        │
        ├──► CRM research_complete hook
        │         │
        │         └──► contacts/ (entities)
        │
        ├──► nlm-podcast-creator
        │         │
        │         └──► Podcasts (daily + topical)
        │
        └──► research-post-processor
                  │
                  ├──► research_learning.org (DONE + :OUTPUT: + :ZETTELS:)
                  ├──► journal update
                  └──► industry-landscape.yaml
```

## Output Locations

| Type | Path |
|------|------|
| Podcasts | `0-personal/content/podcasts/` |
| Research Reports | `0-personal/content/reports/` |
| Literature Notes | `0-personal/notes/2-knowledge/literature/` |
| Zettels | `0-personal/notes/2-knowledge/zettel/` |
| Industry Landscape | `0-personal/notes/2-knowledge/industry-landscape.yaml` |

## Configuration

In `~/.datacore/settings.local.yaml`:

```yaml
research:
  # Podcast settings
  podcast_defaults:
    duration_target: "30min"
    max_sources: 10
    min_sources: 3

  # Daily processing
  daily_processing:
    enabled: true
    min_podcasts: 2
    max_links_per_night: 20

  # Action item extraction
  action_extraction:
    enabled: true
    max_per_source: 5
    default_priority: "B"
    include_next_steps: true

  # Post-processing updates
  post_processing:
    update_research_org: true
    update_journal: true
    update_industry_landscape: true
    add_output_property: true
    add_zettels_property: true

  # CRM integration (requires crm module)
  crm_integration:
    enabled: true
    auto_create_drafts: true
    min_confidence: 0.8
```

## nlm CLI

The module uses `nlm` CLI for NotebookLM integration:

```bash
nlm list                    # List notebooks
nlm create "Title"          # Create notebook
nlm add [id] [url]          # Add source
nlm audio-create [id] "..."  # Generate podcast
nlm audio-download [id] file # Download audio
```

## Integration with Nightshift

The module integrates with nightshift for overnight processing:
1. Nightshift triggers `daily-research-processor` agent
2. Agent processes research_learning.org
3. Generates podcasts and notes
4. Updates morning briefing

## Focus Areas in research_learning.org

- Verity, Datacore, Datafund (core work)
- Trading, Health & Longevity, Personal Development
- Family, Science, GTD & Productivity, Communication
- Business & Strategy, Technology & Innovation, Personal
