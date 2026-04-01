---
summary: "WhatsApp integration — CRM import from chat exports, bidirectional messaging gateway, and notifications."
triggers: ["whatsapp import", "whatsapp sync", "import whatsapp contacts"]
context: on_match
---

# WhatsApp Module

## Purpose

Native WhatsApp integration providing three capabilities: import chat exports into CRM, bidirectional messaging gateway (commands via WhatsApp), and proactive notifications (briefings, reminders, alerts). Uses WAHA (self-hosted WhatsApp HTTP API).

## Quick Start

> Say "whatsapp import" to process chat exports, or "/whatsapp" for the full command interface.

## How It Works

### Import Pipeline

1. Export chat from WhatsApp (iOS/Android)
2. Place `.txt` files in `.datacore/state/whatsapp/exports/`
3. Run `/whatsapp import` — parses messages, extracts contacts, feeds CRM adapter

### Gateway Commands (via WhatsApp)

Send messages to yourself when gateway is active:

`today` (briefing), `inbox: <note>` (capture), `task: <task>`, `who is <name>` (CRM lookup), `search <query>` (datacortex), `remind <text>`, `status`, `help`. Unrecognized messages auto-capture to inbox.

### Notifications

Morning briefing, contact follow-up reminders, nightshift completion alerts (when configured).

## Agents & Commands

| Name | Type | When to use |
|------|------|-------------|
| `whatsapp-import` | agent | Import contacts/interactions from .txt exports |
| `whatsapp-sync` | agent | Sync contacts via WAHA API |
| `/whatsapp` | command | Import, sync, gateway management |

## Key Paths

| Path | Purpose |
|------|---------|
| `.datacore/state/whatsapp/exports/` | Drop .txt exports here |
| `.datacore/state/whatsapp/processed/` | Processed exports |
| `.datacore/state/whatsapp/phone-index.yaml` | Phone to contact mapping |

## Setup

WAHA gateway via Docker:
```bash
docker run -d --name waha -p 3000:3000 -v ~/.datacore/state/whatsapp/sessions:/app/.sessions devlikeapro/waha
```

Settings in `settings.local.yaml`:
```yaml
modules:
  whatsapp:
    waha_url: "http://localhost:3000"
    owner_number: "+YOUR_PHONE_NUMBER"
    allowed_numbers: ["+YOUR_PHONE_NUMBER"]
```

## Boundaries

- **Allowlist only** — only configured phone numbers can send commands.
- **Local only** — WAHA runs on localhost, no external access.
- **Read-only default** — write operations require explicit confirmation.

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
