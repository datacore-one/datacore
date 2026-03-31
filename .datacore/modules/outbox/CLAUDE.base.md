---
summary: "Content routing out of active workspaces — archive, delivery, publish, dispose."
triggers: ["process outbox", "archive files", "search archive", "move to archive"]
context: on_match
---

# Outbox Module

## Purpose

Routes content out of active workspaces into permanent storage. The outbox (`4-outbox/`) is a staging area — the mirror of `0-inbox/`. Archive is the primary destination; delivery, publish, and dispose are planned.

## Quick Start

> Say "process outbox" to route staged content to archive repos, or "search archive" to find archived files.

## How It Works

### Routing Flow

```
[space]/4-outbox/archive/  →  outbox-processor  →  [space]-archive/ (server)
                                                  →  archive-indexer (datacortex embeddings)
```

- Content placed in `4-outbox/archive/` is routed to server archive repos
- Semantic path structure is preserved
- Companion files (`.companion.md`) always travel with their source
- Archive is permanent — no automatic retention cleanup
- Searchable via datacortex snapshot

### Nightshift Schedule

| Task | Schedule | Agent |
|------|----------|-------|
| process-outbox | 2 AM daily | outbox-processor |
| index-archives | 3 AM weekly (Sun) | archive-indexer |

## Agents & Commands

| Name | Type | When to use |
|------|------|-------------|
| `outbox-processor` | agent | Route 4-outbox/ content to archive repos |
| `archive-indexer` | agent | Build datacortex search index for archives |
| `/outbox` | command | Process outbox queue (supports --dry-run, --space) |

Tools: `pending` (list outbox items), `archive_search` (search archives), `dispose` (permanent delete with logging).

## Key Paths

| Path | Purpose |
|------|---------|
| `[space]/4-outbox/archive/` | Staging queue for archive |
| `[space]/4-outbox/_routing.yaml` | Space-specific routing rules |
| Server: `[space]-archive/` | Permanent archive repos |
| `lib/archive_sync.py` | CLI: `--dry-run`, `--space`, `--json` |

## Setup

Configure in `settings.local.yaml`:
```yaml
outbox:
  archive_location: server    # or "local"
  server_host: ""             # SSH host for archive repos
```

## Boundaries

- Does NOT delete content automatically — archive is permanent.
- Delivery, publish, and dispose destinations are planned but not yet active.
- Does NOT handle content coming IN (that is `0-inbox/`).

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
