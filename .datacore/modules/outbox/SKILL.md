---
name: Outbox for Datacore
description: "Content routing — archive to server, search archived content"
version: 1.1.0
author: datacore-one
license: MIT
tags: [outbox, archive, routing, content-lifecycle]
x-datacore:
  module: outbox
  tools: 2
  skills: 1
  agents: 2
  commands: 1
  workflows: 0
  engram_count: 0
  injection_policy: on_match
  match_terms: [outbox, archive, routing, lifecycle, disposal]
---

# Outbox for Datacore

Content routing out of active workspaces — archive historical documents to
server storage, search archived content remotely.

## What This Module Provides

**Tools** (MCP):
- `datacore.outbox.pending` — List items pending in outbox across spaces
- `datacore.outbox.archive_search` — Search archived content on server

**Skills**:
- Archive search

**Agents**:
- `outbox-processor` — Routes outbox content to destinations
- `archive-indexer` — Maintains archive search index

**Commands**:
- `/outbox` — Process outbox queue

## When to Use

Triggers: outbox, archive, routing, lifecycle, disposal.
