---
name: structural-integrity
description: Audit and maintain Datacore folder structure, detect issues, optionally fix
user_invocable: true
aliases:
  - sif
---

# /structural-integrity Command

## Command Context

### When to Reference DIP-0015

**Always reference when:**
- Validating folder hierarchy
- Checking companion file requirements
- Detecting naming violations
- Auditing Git LFS tracking

**Key decisions this DIP informs:**
- Expected folder structure (0-inbox/, 1-tracks/, etc.)
- Companion requirements for non-readable files
- LFS threshold (>10MB)
- Naming convention (kebab-case)

### Quick Reference

| Question | Answer |
|----------|--------|
| Expected structure? | 0-inbox/, 1-tracks/, 3-knowledge/, 4-archive/ |
| Companion required? | .key, .pptx, .psd, .ai files |
| LFS threshold? | Files > 10MB |
| What DIPs govern this? | DIP-0015 (Semantic Org), DIP-0002 (Layers) |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `structural-integrity` | Audit and fix |

### Integration Points

- **DIP-0015** - Folder structure specification
- **/ingest** - Triggers post-ingest check
- **/gtd-weekly-review** - Weekly audit

---

Audit and maintain the organizational structure of Datacore.

## Usage

```
/structural-integrity [check|report|fix]
```

- `check` - Quick scan, return pass/fail with summary (default)
- `report` - Detailed diagnostic of all issues found
- `fix` - Attempt automatic corrections (with confirmation)

## What It Detects

| Issue Type | Severity |
|------------|----------|
| Misplaced files | ⚠️ Warning |
| Missing companions (.key without .md) | ⚠️ Warning |
| Orphaned companions | ❌ Error |
| Missing _index.md | ℹ️ Info |
| Naming violations | ℹ️ Info |
| Unprocessed inbox (>7 days) | ⚠️ Warning |
| Git LFS issues | ❌ Error |
| Broken wiki-links | ⚠️ Warning |

## Examples

```
/structural-integrity              # Quick check
/structural-integrity check        # Same as above
/structural-integrity report       # Detailed report
/structural-integrity fix          # Attempt fixes
/sif                               # Alias for check
```

## Output

### Check Mode
```
STRUCTURAL INTEGRITY CHECK
==========================
Status: ⚠️ WARNINGS FOUND
Errors: 2 | Warnings: 5 | Info: 8

Quick Issues:
❌ 1-teamspace/pitch-deck.key - Missing companion
⚠️ 0-personal/0-inbox/ - 3 items older than 7 days
```

### Report Mode
Detailed breakdown by space with specific file paths and fix instructions.

### Fix Mode
Attempts automatic fixes with confirmation, reports what was fixed vs needs manual attention.

## Agent

Uses `structural-integrity` agent for scanning and fixing.

## Reference

See [DIP-0015: Semantic Organization](../dips/DIP-0015-semantic-organization.md) for full specification.
