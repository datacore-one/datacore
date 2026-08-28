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
| Industry Landscape | `1-datafund/1-tracks/research/Industry landscape.md` |

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
nlm create-audio [id] "..."  # Generate podcast
nlm audio download [id] file # Download audio
```

## Pipeline Entrypoint (canonical since 2026-07-14)

The full pipeline implementation lives HERE, in `lib/research_orchestrator.py`
(moved from the nightshift module, which keeps only a forwarding shim):

```bash
python3 .datacore/modules/research/lib/research_orchestrator.py [--limit N] [--dry-run] [--no-podcast]
```

- Parses url-bearing TODOs from research_learning.org (all collected, sorted
  [#A] first, capped at --limit; URL-less reading digests never consume slots)
- Fetch chain: subscription cookies → Jina → direct → Wayback
- Writes literature notes, zettels, CRM entities, landscape rows; marks items DONE
- Podcast: creates a NotebookLM notebook via `nlm`, adds sources, queues the
  audio overview. Two `nlm` constraints, both of which have silently broken
  this pipeline before:
  - **Audio instructions MUST be empty** — `create-audio <id> ""`. Any custom
    instruction routes to the old RPC, which the server rejects with
    "One or more arguments are invalid". Set instructions in the web UI.
  - Old-style verbs (`create`, `add`, `create-audio`) still work as deprecated
    aliases on v0.1.1, which all hosts now run. `notebook create` / `source add`
    / `audio create` are the current spellings.
- Config: module.yaml settings (`nlm_path`, `podcast_output_dir`,
  `reports_output_dir`, `literature_output_dir`, `zettel_output_dir`,
  `research_org_file`) are wired with fail-safe fallbacks; `NLM_BIN` env
  overrides `nlm_path`

### Agent usage (Winston / Miles / any agent)

- **Run it**: invoke the entrypoint above; `--dry-run` is safe reconnaissance.
- **Check output**: podcasts land in `0-personal/content/podcasts/`, notes per
  Output Locations. A run that reports `Processed: 0` with a non-empty queue,
  or a skipped podcast stage, is a FAILURE signal — surface it, don't stay silent.
- **Update it**: this module is tracked in the root Data repo
  (datacore-one/datacore) — commit + push there. The nightshift repo needs no
  changes for research behavior.
- **Auth**: nlm auth is derived from browser cookies, so it can ONLY be
  refreshed on the Mac — the servers have no browser and can never renew
  themselves. It expires roughly monthly. This is automated:

      .datacore/modules/research/lib/nlm_auth_sync.py check   # status, all hosts
      .datacore/modules/research/lib/nlm_auth_sync.py sync    # refresh + push

  A weekly cron on the Mac (Sun 21:00, ahead of the Monday research run) runs
  `sync`. Left unautomated, both servers aged out on 2026-07-14 and the podcast
  stopped being produced for a month without anyone being told.

## Integration with Nightshift

Nightshift is scheduling only: `nightshift-research.timer` (02:00 UTC) runs
`nightshift run --command=/research-daily`, which dispatches to the entrypoint
above. Results feed the morning briefing.

## Focus Areas in research_learning.org

- Verity, Datacore, Datafund (core work)
- Trading, Health & Longevity, Personal Development
- Family, Science, GTD & Productivity, Communication
- Business & Strategy, Technology & Innovation, Personal
