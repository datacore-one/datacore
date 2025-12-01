---
name: outbox
description: Process the outbox queue and route content to destinations (archive, delivery, publish)
user_invocable: true
---

# /outbox Command

## Command Context

### When to Reference DIP-0017

**Always reference when:**
- Moving content to archive
- Processing `4-outbox/` folders
- Setting up archive repos on nightshift server
- Determining routing destinations

**Key decisions this DIP informs:**
- Content leaving active workspace goes through `4-outbox/`
- Archive is permanent storage, no automatic retention cleanup
- Archive repos live on server (or local for local-only deployment)
- Preserve semantic path structure when archiving

### Quick Reference

| Question | Answer |
|----------|--------|
| What is 4-outbox? | Staging area for content leaving workspace |
| Where do archived files go? | Server archive repos: `[space]-archive/` |
| What about companions? | Always move together with source file |
| Is archive permanent? | Yes, no automatic cleanup |
| What DIPs govern this? | DIP-0017 (Outbox), DIP-0015 (Semantic Org) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `outbox-processor` | Routes content to destinations |
| `archive-indexer` | Updates search index after archiving |

### Integration Points

- **DIP-0017** - Outbox/archive specification
- **DIP-0015** - Semantic organization, folder structure
- **DIP-0011** - Nightshift integration for scheduled processing

---

Process the outbox queue and route content to destinations. The outbox (`4-outbox/`) is the opposite of inbox - content staging for exit from active workspace.

## Usage

```
/outbox                    # Process all spaces
/outbox [space]            # Process specific space
/outbox --dry-run          # Preview without moving
```

## Workflow

### Phase 1: Discovery

1. **Load configuration** from `.datacore/settings.yaml`:
   - `outbox.archive_location` (server|local)
   - `outbox.server_host` (server IP)
   - `outbox.archive_repos` (repo paths)

2. **Scan spaces** for `4-outbox/archive/` content:
   - 0-personal
   - 1-teamspace
   - 2-projectspace
   - (any other spaces)

3. **Report findings**:
   ```
   Outbox Discovery
   ================
   0-personal: 3 files in archive/
   1-teamspace: 2 files in archive/
   2-projectspace: 0 files

   Total: 5 files ready to route
   ```

### Phase 2: Processing

For each file in `4-outbox/archive/`:

1. **Determine semantic path**:
   ```
   Source: 0-personal/4-outbox/archive/3-knowledge/literature/Old-Paper.md
   Target: 0-personal-archive/3-knowledge/literature/Old-Paper.md
   ```

2. **Check for companion**:
   - If `file.pdf.companion.md` exists, include both
   - Never separate source from companion

3. **Route to archive repo**:
   - **Server mode**: SSH + scp to server, commit there
   - **Local mode**: Direct copy to `~/.datacore/archives/`

4. **Update indexes**:
   - Remove entry from source `_index.md`
   - Add entry to archive `_index.md`

5. **Remove from outbox** after successful routing

### Phase 3: Commit and Report

1. **Commit changes** to archive repos:
   ```
   Archive: 3 items from 0-personal

   - 3-knowledge/literature/Old-Paper.md
   - 1-tracks/dev/deprecated-spec.md
   - 3-knowledge/reference/old-tool.md
   ```

2. **Push to remote** (if server mode)

3. **Generate summary**:
   ```
   Outbox Processing Complete
   ==========================

   0-personal:
     Archive: 3 items moved
       - 3-knowledge/literature/Old-Paper.md
       - 1-tracks/dev/deprecated-spec.md
       - 3-knowledge/reference/old-tool.md

   1-teamspace:
     Archive: 2 items moved
       - 1-tracks/legal/expired-nda.pdf (+companion)
       - 1-tracks/legal/old-contract.pdf (+companion)

   Total: 5 items processed, 0 errors
   ```

### Phase 4: Index Update (Optional)

If files were archived, optionally trigger `archive-indexer`:

```
Run archive indexer now?
- [Y] Yes, update search index
- [N] No, wait for scheduled run (3 AM weekly)
```

## Options

| Option | Description |
|--------|-------------|
| `--dry-run` | Show what would be processed without moving |
| `--space <name>` | Process only specified space |
| `--no-index` | Skip archive indexer after processing |
| `--verbose` | Show detailed file operations |

## Examples

```
/outbox                    # Process all outbox folders
/outbox 0-personal         # Only process personal space
/outbox --dry-run          # Preview changes
/outbox --verbose          # Detailed output
```

## Dry Run Output

```
/outbox --dry-run

Dry Run - No files will be moved
================================

0-personal: 3 files would be archived
  - 4-outbox/archive/3-knowledge/literature/Old-Paper.md
    → 0-personal-archive/3-knowledge/literature/Old-Paper.md
  - 4-outbox/archive/1-tracks/dev/deprecated-spec.md
    → 0-personal-archive/1-tracks/dev/deprecated-spec.md
  - 4-outbox/archive/3-knowledge/reference/old-tool.md
    → 0-personal-archive/3-knowledge/reference/old-tool.md

1-teamspace: 2 files would be archived
  - 4-outbox/archive/1-tracks/legal/expired-nda.pdf (+companion)
    → 1-teamspace-archive/1-tracks/legal/expired-nda.pdf (+companion)
  - 4-outbox/archive/1-tracks/legal/old-contract.pdf (+companion)
    → 1-teamspace-archive/1-tracks/legal/old-contract.pdf (+companion)

Total: 5 files would be processed
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

## Error Handling

| Error | Recovery |
|-------|----------|
| Server unreachable | Retry 3x, then skip (items remain in outbox) |
| Archive repo missing | Create repo, initialize, continue |
| File conflict in archive | Rename with timestamp suffix |
| Git push failed | Leave staged, report for manual push |
| Companion missing | Warn but proceed with source only |

## Output Artifacts

| Artifact | Location | When Created |
|----------|----------|--------------|
| Archived files | `[space]-archive/` | All processed files |
| Archive index | `[archive-repo]/_index.md` | Updated with new entries |
| Source index | `4-outbox/_index.md` | Entries removed |
| Commit | Archive repo | After each space processed |

## Nightshift Integration

The `/outbox` command runs automatically via nightshift:

- **Schedule**: 2 AM daily
- **Agent**: `outbox-processor`
- **Followed by**: `archive-indexer` (3 AM weekly)

## Agent

Spawns `outbox-processor` for routing operations.

## Reference

See [DIP-0017: Outbox & Archive Pattern](../../../dips/DIP-0017-outbox-archive-pattern.md) for full specification.

## Related

- [/archive-search](./archive-search.md) - Search archived content
- [outbox-processor](../agents/outbox-processor.md) - Routing agent
- [archive-indexer](../agents/archive-indexer.md) - Index agent
