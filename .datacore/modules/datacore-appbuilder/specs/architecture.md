# Datacore App Framework — Architecture

## Vision

Private, AI-driven desktop and web apps that learn from usage and adapt to each user. Users own their data and identity. Power users can trade knowledge packs on the Katra marketplace.

## Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| Frontend | React + Vite | UI components and routing |
| Styling | Tailwind CSS v4 | Utility-first CSS |
| Desktop | Tauri v2 | Native binary, filesystem, keychain |
| State | TanStack Query | Cache layer over filesystem |
| AI | Anthropic Claude SDK | Content generation, analysis |
| Data | YAML on filesystem | Human-readable, git-friendly storage |
| Testing | Vitest + Playwright | Unit/integration + E2E |

## Core Architecture

### Two Data Paths

**Fast path (milliseconds)** — All UI rendering and interaction:
```
React UI -> TanStack Query -> Tauri FS API -> Local Files
```
No AI. Direct file reads/writes. Lists, forms, saves.

**Smart path (1-5 seconds)** — AI-powered features:
```
React UI -> lib/ai -> Claude API -> Response
```
Content generation, analysis, suggestions.

### State Management

TanStack Query as the data layer between React and filesystem:
- Query functions read from filesystem via Tauri FS API
- Mutations write to filesystem and invalidate relevant queries
- Optimistic updates for UI responsiveness
- No separate state store — TanStack Query IS the cache

```typescript
// Read
const { data } = useQuery({
  queryKey: ['items'],
  queryFn: () => storage.list('items/'),
})

// Write
const mutation = useMutation({
  mutationFn: (item) => storage.write(`items/${item.id}.yaml`, item),
  onSuccess: () => queryClient.invalidateQueries({ queryKey: ['items'] }),
})
```

No `watch()` in Phase 1 — the app is the only writer, so cache invalidation via mutations is sufficient.

## Framework Libraries

### lib/storage — Filesystem Abstraction

```typescript
interface StorageAdapter {
  read(path: string): Promise<string>
  readBytes(path: string): Promise<Uint8Array>
  write(path: string, data: string | Uint8Array): Promise<void>
  delete(path: string): Promise<void>
  exists(path: string): Promise<boolean>
  stat(path: string): Promise<FileStat>
  list(dir: string): Promise<FileEntry[]>
  move(from: string, to: string): Promise<void>
  mkdir(path: string): Promise<void>
}
```

**Implementations:**
- Phase 1: `FilesystemAdapter` (Tauri FS API)
- Phase 2: `IndexedDBAdapter` (web)
- Phase 3: `FairdriveAdapter` (decentralized sync)

**Concurrency:** All writes go through `AsyncMutex` — a serial write queue that prevents YAML corruption from simultaneous read-modify-write. In-memory (lost on crash) but individual file writes, so worst case is a lost write, not corruption.

### lib/ai — Multi-Provider AI + Credits

```typescript
type AIMode = "byok" | "credits" | "proxy"
type ProviderId = "claude" | "openai" | "gemini"

interface AIConfig {
  mode: AIMode
  model: string          // Model ID from catalog
  apiKey?: string        // BYOK: user's own key
  proxyUrl?: string      // Credits/proxy: server endpoint
  proxyToken?: string    // Credits/proxy: auth token
}
```

**Three modes:**
- `byok` — Bring Your Own Key. User pastes their Claude/OpenAI/Gemini API key. Free, unlimited, calls provider directly from the app. Many users already have keys from ChatGPT or Claude subscriptions.
- `credits` — No API key needed. 1 free demo on first use, then buy credit packs ($5/10 credits, $10/25, $30/100). Calls our proxy which deducts credits per call. Different models cost different credits.
- `proxy` — Legacy/licensed mode. Flat-rate or bundled AI calls for licensed users.

**Model catalog:**

| Model | Provider | Credits/call | Quality |
|-------|----------|-------------|---------|
| Claude Sonnet 4 | Claude | 1 | Good for content |
| Claude Opus 4 | Claude | 5 | Best for design/code |
| Claude Haiku 4.5 | Claude | 0.25 | Fast, cheap tasks |
| GPT-4o | OpenAI | 1 | Strong all-round |
| GPT-4o Mini | OpenAI | 0.25 | Fast, cheap tasks |
| Gemini 2.0 Flash | Gemini | 0.25 | Cheapest option |
| Gemini 2.5 Pro | Gemini | 2 | Long context |

Users select their model in settings. Credit cost scales with model capability.

**Credit economics:**
- 1 credit ≈ $0.50 retail / ~$0.05 API cost = ~10x markup
- Covers infrastructure, support, and margin
- BYOK users pay $0 for AI — revenue comes from app purchase only

**Error handling:**
- 30s timeout with user-visible retry prompt
- 402 when credits exhausted (prompt to buy more)
- Exponential backoff on 429 (rate limit)
- Clear error messages for 401 (invalid key), network errors

### lib/bridge — Datacore Detection

```typescript
interface DataDirOptions {
  appName: string          // "Megaphone" -> ~/Megaphone/
  datacorePath: string     // "0-personal/megaphone"
  subdirs?: string[]       // Created on first launch
}
```

On first launch:
1. Check if `~/Data/.datacore/` exists
2. If found: use `~/Data/{datacorePath}/`
3. If not: create `~/{appName}/`

### lib/licensing — Trial + License

```typescript
interface LicenseConfig {
  appId: string       // "com.datacore.megaphone"
  trialDays: number   // 14
}
```

- Trial: timestamp in `.trial` file, countdown
- License: JSON `{app_id, email, timestamp, signature}` in `.license`
- Ed25519 signature verification (when license server is deployed)
- Single `isLicensed` boolean gates write operations

## App Manifest

```typescript
// datacore.config.ts
export default {
  name: "AppName",
  id: "com.datacore.appname",
  version: "0.1.0",
  ai: { provider: "claude" },
  licensing: {
    type: "one-time",
    price: { usd: 49 },
    trial: { days: 14 },
  },
  dataDir: {
    standalone: "~/AppName",
    datacore: "0-personal/appname",
  },
}
```

## App Structure

```
[app-name]/
  src/                      # React UI
    components/             # Reusable UI components
    pages/                  # Route-level pages
    contexts/               # React context providers (AppContext)
    hooks/                  # Custom React hooks (TanStack Query wrappers)
    index.css               # Tailwind entry point
    main.tsx                # Entry + providers
    App.tsx                 # Root component + routing
  lib/                      # Business logic
    storage/                # <- from datacore-appbuilder
    ai/                     # <- from datacore-appbuilder + app-specific generators
    licensing/              # <- from datacore-appbuilder
    bridge/                 # <- from datacore-appbuilder
    models.ts               # App-specific data models
    [domain].ts             # App-specific services
  src-tauri/                # Tauri v2 backend
    src/lib.rs              # Plugin registration
    Cargo.toml              # Rust dependencies
    tauri.conf.json         # Window config, bundle config
    capabilities/           # FS permissions
  templates/                # App-specific HTML/content templates
  prompts/                  # Versioned AI prompt files
  tests/                    # Vitest + Playwright
  datacore.config.ts        # App manifest
  vite.config.ts            # Vite + Tailwind + path aliases
  vitest.config.ts          # Test configuration
  package.json              # Dependencies + scripts
```

## Data Schema Versioning

- Every YAML data file has `schema_version: 1` at the top
- Migration on read: old schema versions migrated in-place, version bumped
- New fields have defaults — forward compatible
- Known ceiling: ~500-1000 files per directory before listing slows

## Tauri v2 Configuration

### Required Plugins (Cargo.toml)
```toml
tauri = { version = "2", features = [] }
tauri-plugin-shell = "2"
tauri-plugin-fs = "2"
```

### Capabilities (capabilities/default.json)
Scope filesystem access to app data directories only:
```json
{
  "permissions": [
    "core:default",
    "shell:allow-open",
    "fs:default",
    { "identifier": "fs:allow-read", "allow": [{ "path": "$HOME/AppName/**" }] },
    { "identifier": "fs:allow-write", "allow": [{ "path": "$HOME/AppName/**" }] }
  ]
}
```

### Scripts (package.json)
```json
{
  "dev": "vite",
  "dev:desktop": "tauri dev",
  "build": "tsc && vite build",
  "build:desktop": "tauri build",
  "test": "vitest run"
}
```

## Runtime Matrix

| Feature | Desktop (P1) | Desktop + Datacore (P2) | Web (P2) | Web + Fairdrive (P3+) |
|---------|-------------|------------------------|----------|----------------------|
| Storage | Local FS | Datacore spaces | IndexedDB | Fairdrive |
| AI | BYOK or Credits proxy | BYOK or Credits proxy | BYOK or Credits proxy | BYOK or Credits proxy |
| MCP/Learning | - | - | - | Phase 3 |
| Identity | None | None | None | fds-id |
| Licensing | File-based | File-based | File-based | File-based |

## Phased Roadmap

### Phase 1: Desktop Apps (current)
- FilesystemAdapter + AsyncMutex
- Multi-provider AIService (Claude, OpenAI, Gemini)
- BYOK + credit system
- Trial/license verification
- Datacore bridge (detect + bootstrap)
- Tauri v2 desktop packaging
- Cloudflare Workers (license server, AI proxy + credits)

### Phase 2: Web + Datacore Integration
- IndexedDBAdapter
- File watching for Datacore-connected mode
- Migration UI (standalone -> Datacore)

### Phase 3: Intelligence Layer
- MCP integration (bundled subprocess)
- Engram learning
- fds-id invisible identity

### Phase 4: Marketplace
- Knowledge packs (engram bundles)
- Katra integration

### Phase 5: Framework Extraction
- Extract `@datacore/storage`, `@datacore/ai`, `@datacore/ui`
- `create-datacore-app` CLI

## Server Infrastructure (Cloudflare Workers + D1)

All server-side logic runs on Cloudflare Workers with D1 (SQLite at edge). No Firebase, no Supabase — these are overkill for local-first desktop apps. Workers solve exactly three problems: license verification, AI proxying, and analytics.

**Why not Firebase/Supabase:** Our apps are local-first (data on disk, works offline). BaaS platforms are cloud-first (database IS the server). Firebase Auth doesn't work in Tauri (`tauri://localhost` rejected). Supabase works but costs $25/mo for features we won't use. Workers cost $0-5/mo and solve what we need.

### Architecture

```
App (Tauri) ──[license token]──> CF Worker ──[our API key]──> Claude API
                                    │
                                 D1 Database
                                 ├── licenses
                                 ├── usage
                                 ├── rate_limits
                                 └── events
```

### Workers

**License Worker** (`lib/workers/license.ts`):
- `POST /webhook` — Payment provider webhook creates license
- `POST /verify` — App validates license on launch
- `POST /activate` — Exchange email for signed license JSON
- Ed25519 signature on license data

**AI Proxy Worker** (`lib/workers/ai-proxy.ts`):
- `POST /v1/generate` — Validates auth, checks credits/rate limit, routes to Claude/OpenAI/Gemini
- `GET /v1/credits` — Check credit balance + free demo status
- `POST /v1/credits/purchase` — Get Stripe Checkout URL for credit pack
- `POST /v1/credits/webhook` — Stripe webhook adds credits after purchase
- `GET /v1/usage` — Usage stats per user

**AI access model — three paths:**
1. **BYOK** — User pastes their own API key. App calls provider directly. We don't proxy, meter, or charge for AI. Most technical users and early adopters take this path.
2. **Credits** — No API key. 1 free demo to try, then buy credit packs ($5 for 10, $10 for 25, $30 for 100). Model selection affects credit cost (Haiku = 0.25, Sonnet = 1, Opus = 5). Proxy routes to the right provider.
3. **Licensed** — For apps with bundled AI (flat-rate pricing). Proxy validates license signature, no credit deduction.

In **BYOK mode**, the proxy is never called. In **credits/licensed mode**, the proxy handles auth, metering, rate limiting, and provider routing.

### D1 Schema

```sql
-- licenses: created by payment webhook, verified on app launch
-- credits: per-user credit balance (email -> balance)
-- usage: per-call tracking (model, tokens, credits used)
-- rate_limits: daily call counter per user
-- events: fire-and-forget analytics
```

See `lib/workers/schema.sql` for full DDL.

### Deployment

```bash
wrangler d1 create appbuilder
wrangler d1 execute appbuilder --file=lib/workers/schema.sql
wrangler secret put SIGNING_KEY
wrangler secret put WEBHOOK_SECRET
wrangler secret put ANTHROPIC_API_KEY
wrangler deploy --config lib/workers/wrangler.toml
```

### When to add Supabase

Only if we later need user accounts with OAuth (cloud sync, support portal) or collaborative features (realtime shared data). These are Phase 3+ concerns.

## Error Handling Strategy

**AI calls:** 30s timeout, exponential backoff on 429, clear messages for auth/network errors. All via toast notifications.

**Filesystem:** Permission denied, disk full, file not found — user-visible messages with recovery actions. Log to `logs/` directory.

**Licensing:** Graceful degradation — trial mode, read-only after expiry, re-activation prompt.

**General:** Every error has a user-visible message and a recovery action. No stack traces in UI. Errors logged for debugging.
