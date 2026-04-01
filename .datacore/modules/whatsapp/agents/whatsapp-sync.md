# whatsapp-sync Agent

Synchronize contacts and interactions from WAHA WhatsApp gateway.


<!-- engram-injection-preamble -->
### Engram Injection

Before starting work, load relevant learned patterns:

1. **Preferred**: Call `plur_inject_hybrid` MCP tool with `prompt` = your task description and `scope` = `agent:whatsapp-sync`
2. **Fallback**: If MCP is unavailable, read `.datacore/state/agent-engrams/whatsapp-sync.md` for compiled engrams

Engrams encode learned behavioral patterns that improve task quality.

## Purpose

Real-time synchronization with WhatsApp via WAHA gateway:
1. Sync contacts from WhatsApp contact list
2. Extract recent interactions for CRM
3. Maintain phone-to-contact mapping

## Prerequisites

- WAHA gateway running (`docker run devlikeapro/waha`)
- Active WhatsApp session (QR code scanned)
- Session status: WORKING

## Trigger

- Manual via `/whatsapp sync`
- Scheduled (if configured)
- After gateway reconnection

## Workflow

### 1. Check Session

```python
from lib import WAHAClient, SessionStatus

client = WAHAClient("http://localhost:3000")
status = await client.get_session_status()

if status != SessionStatus.WORKING:
    print(f"Session not active: {status.value}")
    return
```

### 2. Sync Contacts

```python
# Get contacts from WhatsApp
contacts = await client.get_contacts()

for contact in contacts:
    # Check if exists in CRM
    existing = find_contact_by_phone(contact.phone)

    if not existing:
        # Create new contact
        create_crm_contact(contact)
    else:
        # Update phone index
        update_phone_index(contact.phone, existing.name)
```

### 3. Sync Recent Chats

```python
# Get recent chats
chats = await client.get_chats(limit=50)

for chat in chats:
    if chat.is_group:
        continue  # Skip groups for now

    # Get recent messages
    messages = await client.get_chat_messages(chat.id, limit=20)

    # Extract interactions
    for msg in messages:
        log_interaction(chat, msg)
```

### 4. Update Phone Index

```python
# Build/update phone → contact mapping
phone_index = {}

for contact in contacts:
    crm_contact = find_contact_by_name(contact.name)
    if crm_contact:
        phone_index[contact.phone] = crm_contact.name
    else:
        phone_index[contact.phone] = contact.name

save_phone_index(phone_index)
```

## Input

- WAHA gateway connection
- Optional: date range for interaction sync

## Output

- Updated/created CRM contacts
- Updated phone-index.yaml
- Interaction log

## Configuration

| Setting | Default | Description |
|---------|---------|-------------|
| waha_url | http://localhost:3000 | WAHA gateway URL |
| sync_contacts | true | Sync contact list |
| sync_messages | true | Sync recent messages |
| message_limit | 50 | Messages per chat to sync |
| skip_groups | true | Skip group chats |

## Session Management

If session is not active:

1. **STOPPED**: Start session with `client.start_session()`
2. **SCAN_QR_CODE**: Display QR code, wait for scan
3. **FAILED**: Report error, suggest restart

```python
if status == SessionStatus.SCAN_QR_CODE:
    qr = await client.get_qr_code()
    print("Scan this QR code in WhatsApp:")
    print(qr['value'])

    # Wait for authentication
    while status == SessionStatus.SCAN_QR_CODE:
        await asyncio.sleep(5)
        status = await client.get_session_status()
```

## Interaction Extraction

For CRM adapter integration:

```python
from lib import WhatsAppAdapter
from datetime import datetime, timedelta

adapter = WhatsAppAdapter()

# Sync extracts from WAHA when connected
interactions = adapter.extract_interactions(
    since=datetime.now() - timedelta(days=7)
)

for interaction in interactions:
    print(f"{interaction.date} | {interaction.contact} | {interaction.summary}")
```

## Error Handling

- Session disconnected: Attempt reconnection
- Rate limiting: Back off and retry
- Network errors: Log and skip, continue sync

## Example Session

```
User: /whatsapp sync

Agent: Checking WAHA session...
Session status: WORKING

Syncing contacts...
  Found 127 WhatsApp contacts
  Matched 23 to existing CRM contacts
  Created 12 new contact drafts
  Skipped 92 (low interaction)

Syncing recent messages...
  Processed 45 chats
  Extracted 156 interactions

Updated phone-index.yaml with 127 mappings

Sync complete!
```

## Related

- `whatsapp-import` agent - Export file import
- `/whatsapp gateway` command - Gateway management
- `lib/waha_client.py` - WAHA API client
- DIP-0020 - WhatsApp module specification
