---
name: WhatsApp for Datacore
description: "WhatsApp integration — WAHA gateway, message import, and CRM sync"
version: 0.1.0
author: datacore-one
license: MIT
tags: [whatsapp, messaging, waha, import]
x-datacore:
  module: whatsapp
  tools: 0
  skills: 0
  agents: 2
  commands: 1
  workflows: 0
  engram_count: 0
  injection_policy: on_match
  match_terms: [whatsapp, waha, message, chat]
---

# WhatsApp for Datacore

WhatsApp integration via WAHA gateway — import messages,
sync contacts to CRM, and process chat history.

## What This Module Provides

**Agents**: whatsapp-import, whatsapp-sync

**Commands**: /whatsapp

## When to Use

Triggers: whatsapp, waha, message, chat.
