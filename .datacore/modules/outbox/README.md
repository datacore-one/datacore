# Outbox Module

Content routing out of active workspaces for Datacore.

## Features

- **Archive routing** -- move completed content to archive repos
- **Dispose routing** -- permanent deletion with logging and dry-run
- **Archive search** -- semantic search across archived content
- **Server sync** -- SSH/SCP to server archive repos
- **Companion handling** -- `.companion.md` files move with their source

## Tools

| Tool | Description |
|------|-------------|
| `pending` | List items pending in outbox across spaces |
| `archive_search` | Search archived content via datacortex snapshots |
| `dispose` | Permanently delete files with logging (dry-run default) |

## Folder Structure

```
[space]/4-archive/   -- the space's archive and the processor's queue
  _index.md          -- stays in the space
  <original path>/   -- archived content, under the path it had when active
```

## Installation

Included by default in Datacore. See [CLAUDE.base.md](CLAUDE.base.md) for full documentation.

## Specification

[DIP-0017: Outbox & Archive Pattern](../../dips/DIP-0017-outbox-archive-pattern.md)

## License

MIT
