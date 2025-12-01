# Outbox Module

Routes content out of active workspaces. Archive is the primary destination; other routing (delivery, publish, dispose) is TBD.

## Module Context

### When to Reference This Module

**Always reference when:**
- Moving content to archive
- Processing `4-outbox/` folders
- Setting up archive repos
- Searching archived content

**Key decisions this module informs:**
- Content leaving active workspace goes through `4-outbox/`
- Archive is permanent storage (no automatic retention cleanup)
- Archive repos live on server or local (configurable)
- Semantic path structure is preserved in archives

### Quick Reference

| Question | Answer |
|----------|--------|
| What replaced 4-archive? | `4-outbox/` - staging for routing OUT |
| Where do archived files go? | `4-outbox/archive/` → server archive repo |
| Is archive permanent? | Yes, no automatic cleanup |
| Can I search archives? | Yes, via datacortex snapshot |
| Where are archive repos? | Server: `[space]-archive/` |

## Concept

```
0-inbox/   = Content coming IN  (capture, import, processing queue)
4-outbox/  = Content going OUT  (archive, delivery, publication)
```

The outbox is a **staging area** for content leaving the active workspace. Like inbox items, outbox items are processed and routed to their destinations.

## Commands

### `/outbox`

Process the outbox queue and route content to destinations.

```
/outbox                    # Process all spaces
/outbox 0-personal         # Process specific space
/outbox --dry-run          # Preview without moving
```

**Options:**
- `--dry-run` - Show what would be processed
- `--space <name>` - Process only specified space
- `--no-index` - Skip archive indexer after processing
- `--verbose` - Show detailed output

### `/archive-search`

Search archived content using datacortex snapshots.

```
/archive-search "contract terms"
/archive-search --space 1-teamspace "financial statements"
```

## Agents

### outbox-processor

Routes content from `4-outbox/archive/` to archive repos.

**Skills:** archive-routing, companion-handling, index-management, cross-repo-operations

**Trigger:**
- `/outbox` command
- Nightshift schedule (2 AM daily)

**Workflow:**
1. Load config from `.datacore/settings.yaml`
2. Scan `4-outbox/archive/` in all spaces
3. For each file:
   - Determine semantic destination path
   - Check for companion file (.companion.md)
   - Move file(s) to archive repo
   - Update indexes in both source and destination
4. Commit and push to archive repos
5. Clean processed items from outbox

**Output:** JSON with processed counts and errors

### archive-indexer

Maintains searchable index in archive repos using datacortex embeddings.

**Skills:** embedding-generation, index-management, content-chunking, manifest-maintenance

**Trigger:**
- After outbox-processor completes
- Nightshift schedule (3 AM weekly)
- `/archive-search --reindex`

**Workflow:**
1. Scan archive repo content
2. Generate embeddings for text content
3. Update `_datacortex/` snapshot (archive.db + manifest.yaml)
4. Commit snapshot for sync

## Folder Structure

Each space has an outbox:

```
[N]-[name]/
└── 4-outbox/                # Staging for routing
    ├── _routing.yaml        # Space-specific routing rules
    ├── _index.md            # Current outbox contents
    └── archive/             # Queue for archive repo
```

Archive repos live on the server (or locally if no server):

```
Server:
├── 0-personal-archive/      # Archive for 0-personal
│   ├── CLAUDE.md            # Archive context
│   ├── _index.md            # Searchable catalog
│   ├── _datacortex/         # Search snapshot
│   └── [semantic structure] # Mirrors source paths
├── 1-teamspace-archive/
└── ...
```

## Configuration

In `.datacore/settings.yaml`:

```yaml
outbox:
  # Archive location: "server" or "local"
  archive_location: server

  # Server host for archive repos (if server mode)
  server_host: ""  # Set in settings.local.yaml

  # Local archive path (if local mode)
  local_archive_path: "~/.datacore/archives"

  # Archive repo paths on server
  archive_repos:
    0-personal: "Data/0-personal-archive.git"
    1-teamspace: "Data/1-teamspace-archive.git"
    2-projectspace: "Data/2-projectspace-archive.git"
```

## Archive Criteria

Per DIP-0003 Scaffolding categories:

| Category | Keep Active | Archive When |
|----------|-------------|--------------|
| Identity | Current versions | Superseded by new version |
| Strategy | Current + last revision | Older than 2 revisions |
| Contracts | Active + recently expired | Expired > 1 year |
| Finance | Current + 2 years | Older than 2 years |
| Projects | Active projects | Completed/abandoned |
| Media | Current assets | Superseded versions |
| Personal | Manual decision | User moves to outbox |

**No automatic cleanup** - archive is permanent storage.

## Companion File Handling

When archiving, companions always move with their source:

```
Source files:
  4-outbox/archive/report.pdf
  4-outbox/archive/report.pdf.companion.md

Archive destination:
  [archive-repo]/report.pdf
  [archive-repo]/report.pdf.companion.md
```

## Library

`lib/archive_sync.py` provides:

- `OutboxConfig` - Settings loader
- `ArchiveScanner` - Discovers outbox content
- `ServerArchiver` - SSH/SCP to server repos
- `LocalArchiver` - Local archive repos
- `OutboxProcessor` - Main processing orchestration

**CLI Usage:**
```bash
python .datacore/modules/outbox/lib/archive_sync.py --dry-run
python .datacore/modules/outbox/lib/archive_sync.py --space 0-personal --json
```

## Nightshift Integration

Scheduled tasks in `module.yaml`:

| Task | Schedule | Agent |
|------|----------|-------|
| process-outbox | 2 AM daily | outbox-processor |
| index-archives | 3 AM weekly (Sunday) | archive-indexer |

## Error Handling

| Error | Recovery |
|-------|----------|
| Server unreachable | Retry 3x, then queue for next run |
| Archive repo missing | Create repo, initialize, continue |
| File conflict | Rename with timestamp suffix |
| Git push failed | Retry, then leave staged for manual |

## See Also

- [DIP-0017: Outbox & Archive Pattern](../../dips/DIP-0017-outbox-archive-pattern.md) - Full specification
- [DIP-0015: Semantic Organization](../../dips/DIP-0015-semantic-organization.md) - Folder structure
- [DIP-0011: Nightshift](../../dips/DIP-0011-nightshift-module.md) - Server processing
- [DIP-0004: Knowledge Database](../../dips/DIP-0004-knowledge-database.md) - Datacortex integration
