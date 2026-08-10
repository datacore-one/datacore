# Changelog

All notable changes to Datacore are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/).

## 2.0.0 (2026-07-30)

Agent-ledger mindset (`ENG-2026-0729-016`), adopted in the current Datacore
idiom without a blockchain: signed-capable append-only event logs,
verification contracts, shadow accounting, co-sign, ownership-as-data, and
content-addressed artifacts. Eight phases, each landed behind an
interface-locked plan and expanded into its own DIP at phase start.

- P1: Event ledger substrate — per-actor hash-chained append-only event log,
  deterministic fold, opt-in signing (DIP-0034)
- P2: Job contracts + unified verifier — `job_verify.py` emits `metric.attest`
  events per scheduled job (DIP-0035)
- P3: Config plane (DIP-0036)
- P4: Grounded briefings — fact table + token contract + validator, wiring
  behind `COS_GROUNDED=1` (DIP-0037)
- P5: Action loop + co-sign (DIP-0038)
- P6: Server-first artifacts + live box deploy (DIP-0039)
- P7: Agent consolidation — registry GC + evaluator personas-as-data
  (DIP-0040)
- P8: Executor adapters + shadow accounting + this release (DIP-0041)

### Deferred gates

Recorded honestly as open, not claimed done:

- 7-green-days retirement clock for `cos_verify_morning.sh` — 0/7 as of this
  release (box verify cron running, `job_verify.py --machine box` green)
- `answers.yaml`/`facts.json` producers absent on box (Phase 6 follow-up)
- Agent registry ≤60-active target: actual is 110 (`registry_gc.py --check`),
  down from 138 with 28 archived — further consolidation families are
  follow-up, not forced
- Deploy-side wirings named but not yet flipped: `COS_GROUNDED=1` (grounded
  briefing pipeline), `cos_generate.py`/`cos_reasoning.py` + the nightshift
  call site adopting `get_executor()`, nightshift's evaluator dispatch
  reading `registry/evaluators.yaml`, and `.datacore/modules/nightshift/module.yaml`
  still naming pre-consolidation `evaluator-*` agents

## [1.0.0] - 2026-03-04

First public release.

### Core System
- Layered context pattern with 4-level privacy (DIP-0002)
- Fork-and-overlay contribution model (DIP-0001)
- Semantic folder organization (DIP-0015)
- Tag taxonomy with 11 namespaces (DIP-0014)
- Agent registry with 147 agents across 21 modules (DIP-0016)
- Credential management with audit and rotation tracking (DIP-0018)

### GTD Module
- Complete Getting Things Done workflow (DIP-0009)
- 13 MCP tools for task management
- AI task delegation via :AI: tags
- Daily/weekly/monthly review commands
- Trigger lists for weekly review brainstorming

### Nightshift Module
- Autonomous overnight AI task execution (DIP-0011)
- Multi-persona evaluation panel (21 evaluators)
- Queue optimization with strategic intent scoring
- Scheduler management (launchd/systemd/cron)
- Budget enforcement and cost tracking

### Learning System
- Three-loop learning architecture (DIP-0019)
- Engram capture, absorption, and exchange
- Session learning with pattern extraction
- Engram packs for knowledge distribution

### Research Module
- Three-layer research pipeline (DIP-0021)
- Pluggable source registry (Perplexity, Exa, Google Scholar, Jina, Gemini)
- Knowledge extraction with literature notes and atomic zettels
- Podcast generation via NotebookLM

### Knowledge Management
- Datacortex knowledge graph with semantic search
- Outbox/archive pattern for content routing (DIP-0017)
- Scaffolding pattern for knowledge base structure (DIP-0003)

### Additional Modules
- CRM: Network intelligence with relationship tracking (DIP-0012)
- Meetings: Lifecycle management with transcription processing (DIP-0013)
- Mail: Gmail integration with classification
- 13 private modules available (comms, health, trading, forge, grants, slides, news, telegram, dev, image-generation, personal-finance, verity, whatsapp)

### Infrastructure
- MCP server with dynamic module tool loading (DIP-0022)
- CI/CD with multi-layer PR validation
- Pre-commit hooks for PII detection
- Installation via fork-and-clone with install.yaml manifest (DIP-0005)
