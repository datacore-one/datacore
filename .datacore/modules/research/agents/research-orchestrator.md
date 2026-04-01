---
name: research-orchestrator
description: Module version of research-orchestrator with research module output paths. Coordinates the full research pipeline for both interactive (/research) and overnight (nightshift :AI:research:) execution.
model: sonnet
tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - WebFetch
---

# Research Orchestrator (Module Version)

This is the **research module version** of the core `research-orchestrator` agent. It inherits all behavior from the core agent but uses module-configured output paths.

## Module Output Paths

Read from `module.yaml` settings at startup:

| Setting | Default | Purpose |
|---------|---------|---------|
| `podcast_output_dir` | `0-personal/content/podcasts` | Generated podcasts |
| `reports_output_dir` | `0-personal/content/reports` | Research reports and briefings |
| `literature_output_dir` | `0-personal/notes/2-knowledge/literature` | Literature notes |
| `zettel_output_dir` | `0-personal/notes/2-knowledge/zettel` | Atomic zettels |
| `industry_landscape_file` | `0-personal/notes/2-knowledge/industry-landscape.yaml` | Industry landscape |
| `research_org_file` | `0-personal/org/research_learning.org` | Research queue |

## Reads (at startup)

1. `.datacore/registry/sources.yaml` — available sources
2. `.datacore/settings.yaml` — core research settings
3. `.datacore/modules/research/module.yaml` — module output paths and settings

## Behavior

Follows the core `research-orchestrator` workflow exactly. The only difference is that output paths are resolved from module settings rather than hardcoded.

See [core research-orchestrator](../../../agents/research-orchestrator.md) for full workflow documentation.

## Module-Specific Settings

```yaml
# From module.yaml settings:
daily_processing:
  enabled: true
  min_podcasts: 2

action_extraction:
  enabled: true
  max_per_source: 5
  default_priority: "B"

post_processing:
  update_research_org: true
  update_journal: true
  update_industry_landscape: true
  add_output_property: true
  add_zettels_property: true

crm_integration:
  enabled: true
  auto_create_drafts: true
  min_confidence: 0.8
```
