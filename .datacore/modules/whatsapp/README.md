# WhatsApp Module for Datacore

Native WhatsApp integration providing CRM import, bidirectional messaging, and proactive notifications.

## Features

- **CRM Import**: Parse WhatsApp .txt exports to create contacts
- **Interaction Tracking**: Extract interactions for CRM adapter
- **Bidirectional Gateway**: Send/receive messages, run Datacore commands via WhatsApp
- **Proactive Notifications**: Morning briefings, follow-up reminders, alerts

## Quick Start

### 1. Import WhatsApp Exports

```bash
# Export chats from WhatsApp mobile app
# Place .txt files in:
#   .datacore/state/whatsapp/exports/

# Preview import
/whatsapp import --preview

# Import contacts
/whatsapp import
```

### 2. Set Up WAHA Gateway (Optional)

For bidirectional messaging:

```bash
# Start WAHA with Docker
cd .datacore/modules/whatsapp/templates
docker-compose up -d

# Check logs and scan QR code
docker-compose logs -f waha

# Start gateway
/whatsapp gateway start
```

### 3. Configure Notifications (Optional)

In `.datacore/settings.local.yaml`:

```yaml
modules:
  whatsapp:
    owner_number: "+1234567890"
    allowed_numbers:
      - "+1234567890"
    morning_briefing: true
    morning_briefing_time: "07:00"
```

## Gateway Commands

Send these messages to yourself via WhatsApp:

| Command | Action |
|---------|--------|
| `today` | Get morning briefing |
| `inbox: note` | Capture to inbox |
| `task: task` | Add task |
| `who is name` | CRM lookup |
| `search query` | Search Datacortex |
| `help` | List commands |

## Architecture

```
┌────────────────────────────────────────────────────────┐
│                 WhatsApp Module                         │
├────────────────────────────────────────────────────────┤
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │   Export     │  │   CRM        │  │   Gateway    │  │
│  │   Parser     │  │   Adapter    │  │   Handler    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                 │                 │           │
│         └─────────────────┼─────────────────┘           │
│                           │                             │
│                  ┌────────▼────────┐                    │
│                  │  WAHA Client    │                    │
│                  └────────┬────────┘                    │
│                           │                             │
└───────────────────────────┼─────────────────────────────┘
                            │
                   ┌────────▼────────┐
                   │  WAHA Gateway   │
                   │  (Docker)       │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │  WhatsApp Web   │
                   └─────────────────┘
```

## Files

```
.datacore/modules/whatsapp/
├── module.yaml           # Module manifest
├── CLAUDE.base.md        # Context for Claude
├── README.md             # This file
├── lib/
│   ├── __init__.py
│   ├── whatsapp_export_parser.py
│   ├── whatsapp_adapter.py
│   ├── whatsapp_contact_creator.py
│   ├── waha_client.py
│   ├── whatsapp_gateway.py
│   └── whatsapp_notifications.py
├── commands/
│   └── whatsapp.md
├── agents/
│   ├── whatsapp-import.md
│   └── whatsapp-sync.md
└── templates/
    └── docker-compose.yaml
```

## State Directory

```
.datacore/state/whatsapp/
├── exports/          # Drop .txt exports here
├── processed/        # Moved after import
├── sessions/         # WAHA session data
├── phone-index.yaml  # Phone → contact mapping
└── gateway.log       # Message log
```

## Related

- [DIP-0020](../../dips/DIP-0020-whatsapp-module.md) - Module specification
- [CRM Module](../crm/) - Contact management
- [WAHA Documentation](https://waha.devlike.pro/)

## Security

- Allowlist-based authorization for gateway
- Local-only deployment (no cloud)
- Read-only commands by default
- No sensitive data exposure via WhatsApp
