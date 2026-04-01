---
name: podcast-creator
description: Module version of podcast-creator with research module output paths. Creates NotebookLM podcasts from curated source lists.
model: sonnet
tools:
  - Read
  - Write
  - Bash
---

# Podcast Creator (Module Version)

This is the **research module version** of the core `podcast-creator` agent. It inherits all behavior from the core agent but uses module-configured output paths.

## Module Output Paths

Read from `module.yaml` settings at startup:

| Setting | Default | Purpose |
|---------|---------|---------|
| `podcast_output_dir` | `0-personal/content/podcasts` | Generated podcast files |
| `nlm_path` | `null` (detect from PATH) | Path to nlm CLI binary |

## Module-Specific Settings

```yaml
# From module.yaml settings:
podcast_defaults:
  duration_target: "30min"
  max_sources: 10
  min_sources: 3
```

## Behavior

Follows the core `podcast-creator` workflow exactly. The only difference is that output paths are resolved from module settings.

See [core podcast-creator](../../../agents/podcast-creator.md) for full workflow documentation.
