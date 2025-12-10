# 3-archive

**Purpose**: Historical content, completed projects, and outdated materials.

## Overview

This folder contains content that is no longer actively used but preserved for historical reference, continuity, and learning from past work.

## Structure

### dated/
Time-stamped archives organized by month.

**Format**: `YYYY-MM/archived-YYYY-MM-DD/`

**Purpose**: Old versions of living documents from `1-active/_versions/` that are >6 months old

**Retention**: Keep indefinitely, git history provides additional backup

### backups/
System backups and migration snapshots.

**Contents**:
- `logseq-bak-2025-11-05/` - Original logseq/bak/ folder
- Other system backups

**Purpose**: Safety net during major migrations and restructuring

**Retention**: Keep 1-2 most recent backups, can delete older ones

### deprecated/
Outdated or obsolete content no longer relevant.

**Contents**:
- Empty journal files
- Obsolete documentation
- Failed experiments

**Purpose**: Separated from active content but kept for completeness

**Retention**: Review annually, delete if truly not needed

### Projects

Historical projects organized by name:

#### swarm/
**Period**: Previous employment at Swarm Foundation
**Status**: Historical - on backburner in someday.org
**Files**: ~242 files + 280 bookmarks
**Purpose**: Reference for Fair Data Society and decentralized storage work

#### fds/
**Period**: Fair Data Society work (related to Swarm)
**Status**: Historical reference
**Files**: ~174 files + 177 bookmarks
**Purpose**: Reference for data sovereignty and ethics work

#### beth-campaign/
**Period**: Completed memecoin campaign project
**Status**: Completed
**Files**: ~9 files
**Purpose**: Campaign documentation and learnings

## When to Archive

### From 1-active/
Move to `dated/` when:
- Living document versions are >6 months old
- Project is completed or put on indefinite hold
- Focus area is no longer active

### From 2-knowledge/
Rarely archive knowledge:
- Keep zettels and literature notes (they're permanent)
- Only archive if fundamentally obsolete (outdated technology, superseded concepts)

### From anywhere
Move to `deprecated/` when:
- Content is clearly obsolete
- No longer relevant to any context
- But might have historical value

## Retrieval

Archived content is still searchable:
- Git history preserves all changes
- Tags and links still work
- Can be moved back to active areas if needed

## Maintenance

- **Monthly**: Move old versions from `1-active/_versions/` to `dated/`
- **Quarterly**: Review `deprecated/` for deletion candidates
- **Annually**: Review all archives, delete truly obsolete content

## Special Cases

**Swarm and FDS archives**: These represent significant past work. While not currently active, they contain:
- Valuable frameworks and thinking patterns
- Network and relationship history
- Concepts applicable to current Datafund work

**Keep indefinitely** - may become relevant again or provide valuable reference.

---

*This folder is part of the Data second brain system*
*Last updated: 2025-11-05*
