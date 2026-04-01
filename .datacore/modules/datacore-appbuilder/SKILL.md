---
name: Datacore App Builder
description: Framework for building distributable desktop and web apps on Datacore modules — React + Tauri v2 + Tailwind
version: 0.1.0
author: datacore-one
license: UNLICENSED
tags: [desktop-apps, web-apps, tauri, react, ai, licensing, framework]
x-datacore:
  module: datacore-appbuilder
  tools: 0
  skills: 0
  agents: 0
  commands: 0
  workflows: 0
  engram_count: 0
  injection_policy: on_match
  match_terms: [app, appbuilder, tauri, desktop app, scaffold app, new app]
---

# Datacore App Builder

Framework for building distributable desktop and web apps on Datacore modules.
Local-first architecture: React + Vite + Tailwind for UI, Tauri v2 for desktop
packaging, TanStack Query over filesystem for state, multi-provider AI service.

## What This Module Provides

**Libraries** (`lib/`):
- `lib/storage/` -- StorageAdapter interface, FilesystemAdapter, AsyncMutex
- `lib/ai/` -- AIService (Claude, OpenAI, Gemini), model catalog, credit system
- `lib/licensing/` -- Trial clock, license verification (Ed25519)
- `lib/bridge/` -- Datacore detection and data directory bootstrap
- `lib/workers/` -- Cloudflare Workers: license server, AI proxy + credits, D1 schema

**Scaffold** (`scaffold/`):
- `datacore.config.ts` -- App manifest template
- `package.json` -- Dependencies and scripts template

**Specs** (`specs/`):
- `architecture.md` -- Full architecture specification

## When to Use This Module

Use datacore-appbuilder when:
- Building a new desktop or web app that integrates with Datacore
- Need local-first storage with optional Datacore space detection
- Need multi-provider AI with BYOK + credit system
- Need trial/license verification for distributable apps
- Need Cloudflare Worker infrastructure for license + AI proxy

## Key Architecture Decisions

- **Two data paths**: Fast path (filesystem, ms) for UI, Smart path (AI, 1-5s) for intelligence
- **TanStack Query IS the cache** -- no separate state store
- **All writes serialized** through AsyncMutex (prevents YAML corruption)
- **Datacore bridge**: auto-detect `~/Data/.datacore/`, else standalone `~/AppName/`
- **No Firebase/Supabase** -- local-first apps do not need cloud-first backends
- **Schema versioning**: every YAML file has `schema_version: N`, migrated on read

## Creating a New App

1. Copy scaffold from `datacore-appbuilder/scaffold/`
2. Customize `datacore.config.ts` with app name, ID, pricing
3. Copy needed `lib/` modules into your app
4. Add app-specific pages, hooks, and templates
5. `bun run dev` for web, `bun run dev:desktop` for Tauri
