# /whatsapp

WhatsApp integration menu for Datacore.

## Usage

```
/whatsapp [subcommand]
```

## Subcommands

### Import
Process .txt chat exports from WhatsApp.

```
/whatsapp import [--preview] [--space SPACE]
```

**Options:**
- `--preview` - Show what would be imported without creating files
- `--space` - Target space (default: 0-personal)

**Workflow:**
1. Export chats from WhatsApp mobile app
2. Place `.txt` files in `.datacore/state/whatsapp/exports/`
3. Run `/whatsapp import`
4. Review created contacts in `contacts/people/`

### Sync
Sync contacts from WAHA gateway (requires active session).

```
/whatsapp sync
```

### Gateway
Manage WhatsApp message gateway.

```
/whatsapp gateway [start|stop|status]
```

**Commands:**
- `start` - Start message listener
- `stop` - Stop message listener
- `status` - Show gateway status

### Send
Send message to a contact.

```
/whatsapp send "<contact_name>" "<message>"
```

**Example:**
```
/whatsapp send "Ahmed Bin Sulayem" "Following up on our Davos conversation..."
```

### Stats
Show WhatsApp export statistics.

```
/whatsapp stats
```

## Menu Mode

When run without subcommand, shows interactive menu:

1. **Import exports** - Process .txt chat exports
2. **Sync contacts** - Sync from WAHA gateway
3. **Start gateway** - Start message listener
4. **Stop gateway** - Stop message listener
5. **Send message** - Send to contact
6. **Status** - Show gateway and session status

## Examples

```bash
# Preview what would be imported
/whatsapp import --preview

# Import to specific space
/whatsapp import --space 1-teamspace

# Check gateway status
/whatsapp gateway status

# Send follow-up message
/whatsapp send "Brett Krause" "Great meeting at Davos! Would love to connect about the gaming fund..."
```

## Instructions

When user runs `/whatsapp`:

1. **Without subcommand**: Show numbered menu and ask what they'd like to do
2. **With subcommand**: Execute that action directly

For import operations:
1. Check if exports exist in `.datacore/state/whatsapp/exports/`
2. Parse exports using `WhatsAppExportParser`
3. Create contacts using `WhatsAppContactCreator`
4. Report created/matched/skipped counts

For gateway operations:
1. Check WAHA session status
2. Start/stop session as requested
3. If `SCAN_QR_CODE`, prompt user to scan in WhatsApp mobile app

For send operations:
1. Resolve contact name to phone number
2. Confirm message before sending
3. Send via WAHA client

## Agent Context

**Related agents:**
- `whatsapp-import` - Batch import processing
- `whatsapp-sync` - WAHA synchronization

**Python libraries:**
- `lib/whatsapp_export_parser.py` - Export parsing
- `lib/whatsapp_adapter.py` - CRM adapter
- `lib/whatsapp_contact_creator.py` - Contact creation
- `lib/waha_client.py` - WAHA API client
- `lib/whatsapp_gateway.py` - Message gateway
