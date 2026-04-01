---
name: Research for Datacore
description: "Automated research — source processing, podcast generation, and knowledge extraction"
version: 0.2.0
author: datacore-one
license: MIT
tags: [research, podcasts, readwise, knowledge-extraction, notebooklm]
x-datacore:
  module: research
  tools: 2
  skills: 2
  agents: 2
  commands: 1
  workflows: 0
  engram_count: 0
  injection_policy: on_match
  match_terms: [research, podcast, readwise, sources, literature, zettel, notebooklm]
---

# Research for Datacore

Automated research processing — curate links, generate NotebookLM podcasts,
extract knowledge artifacts, and build industry landscapes.

Tagline: "Curate links. Wake up to podcasts and insights."

## What This Module Provides

**Tools** (MCP):
- `datacore.research.queue` — List pending research links and processing status
- `datacore.research.sources` — Manage and query research source registry

**Skills**:
- Research status checking
- Ad-hoc podcast creation

**Agents**:
- `research-orchestrator` — Full pipeline orchestration (DIP-0021)
- `podcast-creator` — NotebookLM podcast generation

**Commands**:
- `/research-daily` — Manually trigger daily research processing

## When to Use

Triggers: research, podcast, readwise, sources, literature, zettel, notebooklm.
