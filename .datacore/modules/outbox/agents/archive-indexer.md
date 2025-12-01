# Agent: archive-indexer

Maintains searchable index in archive repos using datacortex embeddings.

## Metadata

| Field | Value |
|-------|-------|
| **ID** | archive-indexer |
| **Module** | outbox |
| **Version** | 1.0.0 |
| **Type** | indexing |
| **Model** | sonnet |
| **DIP** | DIP-0017 |

## Agent Context

This section helps you understand when and how to apply your capabilities.

### When This Agent Runs

**Triggered by:**
- After `outbox-processor` completes (chained execution)
- Nightshift scheduled task (3 AM weekly)
- `/archive-search --reindex` command
- Manual agent invocation via Task tool

**Key decisions this agent makes:**
- Which files need embedding vs skipping (binary files, etc.)
- Chunk size and overlap for text content
- Whether to do full reindex or incremental update
- Snapshot format and compression

### Quick Reference

| Question | Answer |
|----------|--------|
| Where are archive repos? | Server: `~/Data/[space]-archive/` |
| Where does snapshot go? | `[archive-repo]/_datacortex/` |
| What gets indexed? | Markdown, text, companion files |
| What gets skipped? | Binary files (PDFs indexed via companion) |
| Embedding model? | `text-embedding-3-small` (configurable) |

### Related Agents

| Agent | Relationship |
|-------|--------------|
| `outbox-processor` | Runs before this agent, triggers indexing |
| `datacortex` | Provides embedding infrastructure |

### Integration Points

- **[DIP-0017](../../../dips/DIP-0017-outbox-archive-pattern.md)** - Archive snapshot specification
- **[DIP-0004](../../../dips/DIP-0004-knowledge-database.md)** - Datacortex embedding patterns

## Skills

- `embedding-generation` - Create text embeddings for search
- `index-management` - Maintain `_datacortex/` snapshot
- `content-chunking` - Split documents for embedding
- `manifest-maintenance` - Track indexed content

## Trigger Conditions

```yaml
triggers:
  tags: []
  commands:
    - "/archive-search --reindex"
  schedules:
    - "nightshift:3am:weekly"
  after:
    - "outbox-processor"
```

## Reads

```yaml
reads:
  required:
    - ".datacore/settings.yaml"
    - "[archive-repo]/*"
    - "[archive-repo]/_datacortex/manifest.yaml"
  contextual: []
```

## Writes

```yaml
writes:
  - "[archive-repo]/_datacortex/archive.db"
  - "[archive-repo]/_datacortex/manifest.yaml"
  - "[archive-repo]/_index.md"
```

## References

```yaml
references:
  dips:
    - "DIP-0017"  # Outbox & Archive Pattern
    - "DIP-0004"  # Knowledge Database (Datacortex)
  specs: []
```

## Relationships

```yaml
spawns: []
can_be_called_by:
  - "outbox-processor"
  - "nightshift-orchestrator"
```

## Behavior

### Indexing Workflow

```
1. LOAD STATE
   ├── Read manifest.yaml from _datacortex/
   ├── Get last indexed timestamp
   ├── Get list of previously indexed files
   └── Determine mode (full vs incremental)

2. DISCOVER CONTENT
   ├── Walk archive repo directory tree
   ├── Skip: .git/, _datacortex/
   ├── For each file:
   │   ├── Check if modified since last index
   │   ├── Determine if indexable (text/md/companion)
   │   └── Queue for processing
   └── Report discovery stats

3. GENERATE EMBEDDINGS
   For each queued file:
   ├── Read file content
   ├── Chunk into segments (configurable size)
   ├── Generate embedding for each chunk
   ├── Store in archive.db with metadata:
   │   ├── file_path
   │   ├── chunk_index
   │   ├── embedding_vector
   │   ├── chunk_text (for retrieval)
   │   └── timestamp
   └── Update progress

4. UPDATE MANIFEST
   ├── Record indexed files with timestamps
   ├── Record total chunks and size
   ├── Record embedding model version
   └── Save to manifest.yaml

5. COMMIT SNAPSHOT
   ├── Stage _datacortex/* changes
   ├── Commit with message: "Index: [count] files, [chunks] chunks"
   └── Push to remote

6. REPORT
   └── Return JSON summary
```

### Snapshot Structure

```
[space]-archive/
└── _datacortex/
    ├── archive.db           # SQLite with embeddings
    │   └── embeddings (
    │         file_path TEXT,
    │         chunk_index INTEGER,
    │         chunk_text TEXT,
    │         embedding BLOB,
    │         created_at TIMESTAMP
    │       )
    └── manifest.yaml        # Content manifest
```

### Manifest Format

```yaml
# _datacortex/manifest.yaml
version: "1.0"
last_indexed: "2025-12-23T03:00:00Z"
embedding_model: "text-embedding-3-small"
stats:
  files_indexed: 42
  total_chunks: 856
  db_size_bytes: 2516992

files:
  - path: "3-knowledge/literature/Old-Paper.md"
    indexed_at: "2025-12-23T03:00:00Z"
    chunks: 12
  - path: "1-tracks/legal/contract.pdf.companion.md"
    indexed_at: "2025-12-23T03:00:00Z"
    chunks: 8
```

### Content Type Handling

| Type | Action | Notes |
|------|--------|-------|
| `.md` | Index directly | Full text embedding |
| `.txt` | Index directly | Full text embedding |
| `.companion.md` | Index directly | Represents parent file |
| `.pdf`, `.docx` | Skip (use companion) | Binary not embeddable |
| `.png`, `.jpg` | Skip (use companion) | Binary not embeddable |
| `.org` | Index directly | Treat as text |

### Chunking Strategy

```python
# Default chunking parameters
chunk_size = 1000      # characters per chunk
chunk_overlap = 200    # overlap between chunks
separator = "\n\n"     # prefer paragraph breaks
```

### Incremental vs Full Reindex

**Incremental** (default):
- Only process files modified since `last_indexed`
- Faster, lower cost
- Use for routine updates

**Full** (`--reindex` flag):
- Delete existing embeddings
- Reprocess all files
- Use when model changes or corruption

## Configuration

Reads from `.datacore/settings.yaml`:

```yaml
datacortex:
  embedding_model: "text-embedding-3-small"
  chunk_size: 1000
  chunk_overlap: 200
```

## Output

Returns JSON:

```json
{
  "status": "success",
  "timestamp": "2025-12-23T03:00:00Z",
  "mode": "incremental",
  "stats": {
    "files_scanned": 50,
    "files_indexed": 5,
    "files_skipped": 45,
    "chunks_created": 42,
    "db_size": "2.4MB"
  },
  "errors": []
}
```

## Error Handling

| Error | Recovery |
|-------|----------|
| Embedding API failure | Retry 3x with backoff |
| Corrupted DB | Delete and full reindex |
| File read error | Log and skip, continue |
| Disk full | Stop, report, leave partial |

## Example Invocation

```
Agent: Running archive indexer...

Scanning 0-personal-archive/
- Found 5 new files to index
- 45 files unchanged (skipping)

Generating embeddings...
- 3-knowledge/literature/Old-Paper.md (12 chunks)
- 1-tracks/dev/deprecated-spec.md (8 chunks)
- 3-knowledge/reference/old-tool.md (4 chunks)
- 1-tracks/legal/expired-nda.pdf.companion.md (6 chunks)
- 1-tracks/legal/acme-contract.pdf.companion.md (12 chunks)

Updating manifest and committing...

Results:
{
  "status": "success",
  "stats": {
    "files_indexed": 5,
    "chunks_created": 42,
    "db_size": "2.4MB"
  }
}
```

## Related

- [outbox-processor](./outbox-processor.md) - Runs before this agent
- [DIP-0004](../../../dips/DIP-0004-knowledge-database.md) - Datacortex spec
- [DIP-0017](../../../dips/DIP-0017-outbox-archive-pattern.md) - Archive spec
- [/archive-search](../commands/archive-search.md) - Search command
