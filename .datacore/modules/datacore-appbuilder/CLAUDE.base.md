---
summary: "Framework for building desktop/web apps on Datacore — React + Tauri v2 + Tailwind"
triggers: ["build app", "new app", "scaffold app", "desktop app", "tauri app"]
context: on_match
---

# Appbuilder Module

## Purpose

Library/framework module for building distributable desktop and web apps on top of Datacore. Provides reusable storage, AI, licensing, and bridge layers. Apps built with it are separate projects that import from `lib/`.

## Quick Start
> Say "scaffold a new desktop app" to start. Copy `scaffold/` directory and customize `datacore.config.ts`.

## How It Works

### Stack
React + Vite + Tailwind CSS v4 (frontend), Tauri v2 (desktop), TanStack Query (state), Cloudflare Workers + D1 (server).

### Two Data Paths
- **Fast path (ms)**: React UI -> TanStack Query -> Tauri FS API -> local YAML files
- **Smart path (1-5s)**: React UI -> lib/ai -> provider API or proxy -> response

### Core Libraries

| Library | Purpose |
|---------|---------|
| `lib/storage/` | StorageAdapter, FilesystemAdapter, AsyncMutex |
| `lib/ai/` | AIService (Claude/OpenAI/Gemini), model catalog, credits |
| `lib/licensing/` | Trial clock, license verification |
| `lib/bridge/` | Datacore detection and bootstrap |
| `lib/workers/` | CF Workers: license server, AI proxy, D1 schema |

### AI Access Model
- **BYOK** — user's own API key, direct provider calls
- **Credits** — 1 free demo, then buy packs; proxy meters usage
- **Proxy** — licensed/flat-rate, proxy validates without credit deduction

### Key Patterns
- All filesystem writes serialized through AsyncMutex (prevents YAML corruption)
- TanStack Query IS the cache — no separate state store
- Schema versioning: every YAML file has `schema_version: N`, migrated on read
- Datacore bridge: auto-detect `~/Data/.datacore/`, else standalone `~/AppName/`

## Agents & Commands

None — this is a pure library/framework module.

## Key Paths

| Path | Purpose |
|------|---------|
| `scaffold/` | Template for new apps |
| `lib/` | Reusable libraries |
| `specs/architecture.md` | Full architecture specification |

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
