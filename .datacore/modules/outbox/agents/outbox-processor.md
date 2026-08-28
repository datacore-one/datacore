# Agent: outbox-processor

Routes content from `4-outbox/` to destinations (archive, delivery, publish, dispose).

## Metadata

| Field | Value |
|-------|-------|
| **ID** | outbox-processor |
| **Module** | outbox |
| **Version** | 1.0.0 |
| **Type** | routing |
| **Model** | sonnet |
| **DIP** | DIP-0017 |


<!-- engram-injection-preamble -->
### Engram Injection

Before starting work, load relevant learned patterns:

1. **Preferred**: Call `plur_admin` MCP tool with `action` = `"plur_inject_hybrid"`, `prompt` = your task description, `scope` = `agent:outbox-processor`
2. **Fallback**: If MCP is unavailable, read `.datacore/state/agent-engrams/outbox-processor.md` for compiled engrams

Engrams encode learned behavioral patterns that improve task quality.

## Agent Context

This section helps you understand when and how to apply your capabilities.

### When This Agent Runs

**Triggered by:**
- `/outbox` command invocation
- Nightshift scheduled task (2 AM daily)
- Manual agent invocation via Task tool

**Key decisions this agent makes:**
- Which files in `4-outbox/archive/` are ready to route
- Semantic path preservation when moving to archive repo
- Companion file handling (move together with source)
- Index updates in both source and destination

### Quick Reference

| Question | Answer |
|----------|--------|
| Where do I read from? | `*/4-outbox/archive/` in all spaces |
| Where do I write to? | Archive repos on server (or local) |
| What config do I need? | `.datacore/settings.yaml` → `outbox` section |
| What about companions? | Always move with source file |
| How do I know archive location? | `outbox.archive_location` in settings |

### Related Agents

| Agent | Relationship |
|-------|--------------|
| `archive-indexer` | Runs after this agent to update search index |
| `structural-integrity` | Validates outbox folder structure |
| `ingest-processor` | May route content to outbox |

### Integration Points

- **[DIP-0017](../../../dips/DIP-0017-outbox-archive-pattern.md)** - Outbox/archive specification
- **[DIP-0015](../../../dips/DIP-0015-semantic-organization.md)** - Folder structure
- **[DIP-0011](../../../dips/DIP-0011-nightshift-module.md)** - Scheduled execution

## Skills

- `archive-routing` - Move content to archive repos
- `companion-handling` - Keep source + companion together
- `index-management` - Update _index.md files
- `cross-repo-operations` - Work across git repositories

## Trigger Conditions

```yaml
triggers:
  tags: []
  commands:
    - "/outbox"
  schedules:
    - "nightshift:2am"
```

## Reads

```yaml
reads:
  required:
    - ".datacore/settings.yaml"
    - "*/4-outbox/archive/"
    - "*/4-outbox/_routing.yaml"
  contextual: []
```

## Writes

```yaml
writes:
  - "*/4-outbox/archive/*"          # Removes processed files
  - "*/4-outbox/_index.md"          # Updates outbox index
  - "[archive-repo]/*"              # Adds archived content
  - "[archive-repo]/_index.md"      # Updates archive index
```

## References

```yaml
references:
  dips:
    - "DIP-0017"  # Outbox & Archive Pattern
    - "DIP-0015"  # Semantic Organization
  specs: []
```

## Relationships

```yaml
spawns: []
can_be_called_by:
  - "nightshift-orchestrator"
```

## Behavior

### Archive Routing Workflow

```
1. LOAD CONFIGURATION
   ├── Read .datacore/settings.yaml
   ├── Get outbox.archive_location (server|local)
   ├── Get archive repo paths from outbox.archive_repos
   └── Validate server connectivity (if server mode)

2. DISCOVER SPACES
   ├── Find all */4-outbox/archive/ directories
   ├── Filter to spaces with content to process
   └── Report discovered items count

3. FOR EACH SPACE:
   ├── List files in 4-outbox/archive/
   ├── For each file:
   │   ├── Determine semantic destination path
   │   ├── Check for companion file (.companion.md)
   │   ├── If companion exists, include in move
   │   └── Queue for processing
   └── Continue to next space

4. PROCESS QUEUE
   For each queued item:
   ├── Connect to archive repo (clone/fetch if needed)
   ├── Create destination directory structure
   ├── Move file(s) to archive repo
   ├── Update archive _index.md (add entry)
   ├── Update source _index.md (remove entry)
   ├── Remove from 4-outbox/archive/
   └── Stage changes

5. COMMIT AND PUSH
   For each archive repo with changes:
   ├── Commit with message: "Archive: [count] items from [space]"
   ├── Push to remote
   └── Log success

6. REPORT
   └── Return JSON summary
```

### Semantic Path Preservation

Files maintain their semantic location within the archive:

```
Source: 0-personal/4-outbox/archive/3-knowledge/literature/Old-Paper.md
Target: 0-personal-archive/3-knowledge/literature/Old-Paper.md

Source: 1-teamspace/4-outbox/archive/1-tracks/legal/contracts/Acme-2018.pdf
Target: 1-teamspace-archive/1-tracks/legal/contracts/Acme-2018.pdf
```

### Companion File Handling

If a file has a companion (`.companion.md`), both move together:

```
Source files:
  4-outbox/archive/report.pdf
  4-outbox/archive/report.pdf.companion.md

Archive destination:
  [archive-repo]/report.pdf
  [archive-repo]/report.pdf.companion.md
```

### Index Updates

**Source index removal:**
```markdown
<!-- In 4-outbox/_index.md, remove: -->
- [ ] `archive/3-knowledge/literature/Old-Paper.md` - Ready for archive
```

**Archive index addition:**
```markdown
<!-- In [archive-repo]/_index.md, add: -->
## 2025-12-23

- `3-knowledge/literature/Old-Paper.md` - Archived from 0-personal
```

### Server vs Local Mode

**Server mode** (`outbox.archive_location: server`):
```bash
# SSH to server, clone/update archive repo
ssh user@server "cd ~/Data/[space]-archive && git pull"
# Copy files via scp/rsync
scp file user@server:~/Data/[space]-archive/path/
# Commit on server
ssh user@server "cd ~/Data/[space]-archive && git add -A && git commit -m 'Archive: ...' && git push"
```

**Local mode** (`outbox.archive_location: local`):
```bash
# Archive repos at ~/.datacore/archives/[space]-archive
cd ~/.datacore/archives/[space]-archive
# Direct file operations
cp source destination
git add -A && git commit -m "Archive: ..."
```

## Configuration

Reads from `.datacore/settings.yaml`:

```yaml
outbox:
  archive_location: server    # or "local"
  server_host: "your-server-ip"  # Set in settings.local.yaml
  local_archive_path: "~/.datacore/archives"
  archive_repos:
    0-personal: "Data/0-personal-archive.git"
    1-teamspace: "Data/1-teamspace-archive.git"
    2-projectspace: "Data/2-projectspace-archive.git"
```

## Output

Returns JSON:

```json
{
  "status": "success",
  "timestamp": "2025-12-23T02:00:00Z",
  "processed": {
    "archive": {
      "0-personal": 3,
      "1-teamspace": 2,
      "2-projectspace": 0
    }
  },
  "total": 5,
  "errors": []
}
```

## Error Handling

| Error | Recovery |
|-------|----------|
| Server unreachable | Retry 3x, then queue for next run |
| Archive repo missing | Create repo, initialize, continue |
| File conflict | Rename with timestamp suffix |
| Git push failed | Retry, then leave staged for manual |

## Example Invocation

```
User: /outbox

Agent: Scanning outbox folders...

Found 5 items to archive:
- 0-personal: 3 files
- 1-teamspace: 2 files

Processing...
- Archived: 0-personal/3-knowledge/literature/Old-Paper.md
- Archived: 0-personal/1-tracks/dev/deprecated-spec.md
- Archived: 0-personal/3-knowledge/reference/old-tool.md
- Archived: 1-teamspace/1-tracks/legal/expired-nda.pdf
- Archived: 1-teamspace/1-tracks/legal/expired-nda.pdf.companion.md

Results:
{
  "status": "success",
  "total": 5,
  "errors": []
}
```

## Related

- [archive-indexer](./archive-indexer.md) - Updates search index after archiving
- [DIP-0017](../../../dips/DIP-0017-outbox-archive-pattern.md) - Specification
- [/outbox command](../commands/outbox.md) - User-facing command
