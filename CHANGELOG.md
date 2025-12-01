# Changelog

All notable changes to Datacore are documented here.

Format based on [Keep a Changelog](https://keepachangelog.com/).

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
