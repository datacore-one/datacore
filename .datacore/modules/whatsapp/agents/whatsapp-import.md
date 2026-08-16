# whatsapp-import Agent

Import contacts and interactions from WhatsApp .txt exports.


<!-- engram-injection-preamble -->
### Engram Injection

Before starting work, load relevant learned patterns:

1. **Preferred**: Call `plur_admin` MCP tool with `action` = `"plur_inject_hybrid"`, `prompt` = your task description, `scope` = `agent:whatsapp-import`
2. **Fallback**: If MCP is unavailable, read `.datacore/state/agent-engrams/whatsapp-import.md` for compiled engrams

Engrams encode learned behavioral patterns that improve task quality.

## Purpose

Process WhatsApp chat exports to:
1. Create CRM contacts from chat participants
2. Extract interaction history for CRM adapter
3. Build phone-to-contact mapping index

## Trigger

- Manual via `/whatsapp import`
- When new files appear in `.datacore/state/whatsapp/exports/`

## Workflow

### 1. Discover Exports

```python
from pathlib import Path
from lib import WhatsAppExportParser, parse_export_directory

exports_dir = Path.home() / 'Data' / '.datacore' / 'state' / 'whatsapp' / 'exports'
exports = parse_export_directory(exports_dir)
```

### 2. Preview Import

Before creating contacts, show preview:

```python
from lib import WhatsAppContactCreator

creator = WhatsAppContactCreator()
preview = creator.get_import_preview()

print(f"Exports: {preview['export_count']}")
print(f"Contacts to create: {preview['candidate_count']}")

for c in preview['candidates'][:10]:
    print(f"  {c['name']}: {c['message_count']} messages")
```

### 3. Create Contacts

```python
results = creator.create_contacts_from_exports(
    space='0-personal',
    dry_run=False
)

print(f"Created: {len(results['created'])}")
print(f"Matched existing: {len(results['matched'])}")
print(f"Skipped (low activity): {len(results['skipped'])}")
```

### 4. Post-Processing

After successful import:
1. Move processed exports to `processed/` subdirectory
2. Update phone-index.yaml with new mappings
3. Report results to user

## Input

- Export files in `.datacore/state/whatsapp/exports/*.txt`
- Optional: specific file path to import

## Output

- CRM contact files in `[space]/contacts/people/`
- Updated phone-index.yaml
- Import summary report

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| space | 0-personal | Target space for contacts |
| min_messages | 3 | Minimum messages to create contact |
| dry_run | false | Preview without creating files |

## Contact Creation Rules

1. **Minimum activity**: 3+ messages to create contact
2. **Duplicate handling**: Match by name or phone, update stats
3. **Relevance scoring**:
   - 100+ messages → relevance 4
   - 30-99 messages → relevance 3
   - 10-29 messages → relevance 2
   - 3-9 messages → relevance 1

## Export Formats

Handles both iOS and Android export formats:

**iOS**: `[DD/MM/YYYY, HH:MM:SS] Name: Message`
**Android**: `DD/MM/YYYY, HH:MM - Name: Message`

Automatically detects format based on pattern matching.

## Error Handling

- Skip malformed lines (logged to stderr)
- Continue on file read errors
- Report partial success with error details

## Example Session

```
User: /whatsapp import --preview

Agent: Found 5 exports in .datacore/state/whatsapp/exports/

Preview:
  - Ahmed Bin Sulayem: 47 messages
  - Brett Krause: 23 messages
  - Micah Smith: 18 messages
  - Peter Ford: 12 messages
  - Samuel Stubblefield: 8 messages
  ... and 3 more

Would you like to proceed with import?

User: yes

Agent: Importing contacts...

Results:
  ✓ Created: 6 contacts
  ↔ Matched existing: 2 contacts
  ⊘ Skipped: 1 contact (low activity)

Exports moved to processed/
```

## Related

- `whatsapp-sync` agent - WAHA gateway sync
- `/whatsapp` command - Interactive interface
- DIP-0020 - WhatsApp module specification
