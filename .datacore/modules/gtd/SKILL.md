---
name: GTD for Datacore
description: "Getting Things Done — task capture, inbox processing, and org-mode management"
version: 1.0.0
author: datacore-one
license: MIT
tags: [gtd, tasks, inbox, org-mode, productivity]
x-datacore:
  module: gtd
  tools: 4
  skills: 1
  agents: 4
  commands: 4
  workflows: 3
  engram_count: 0
  injection_policy: on_match
  match_terms: [inbox, task, todo, gtd, next action, review, weekly review, delegate]
---

# GTD for Datacore

Getting Things Done methodology with org-mode as the task management substrate.

## What This Module Provides

**Tools** (MCP):
- `datacore.gtd.inbox_count` — Count items across space inboxes
- `datacore.gtd.add_task` — Add task to inbox.org
- `datacore.gtd.list_next_actions` — List pending tasks from next_actions.org
- `datacore.gtd.complete_task` — Mark task as DONE

**Skills**:
- Inbox triage decision tree

**Agents**:
- Inbox processor, content writer, data analyzer, project manager

**Commands**:
- `/today`, `/tomorrow`, `/wrap-up`, `/continue`

## When to Use

Triggers: inbox, task, todo, gtd, next action, review, weekly review, delegate.
