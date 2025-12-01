---
name: research
description: Deep multi-source research pipeline. Discovers sources, processes content, synthesizes reports, and optionally generates podcasts.
user_invocable: true
---

# /research Command

## Command Context

### When to Reference DIP-0021

**Always reference when:**
- Running research pipelines
- Discovering external sources
- Processing multiple URLs
- Generating research reports and podcasts

**Key decisions this DIP informs:**
- Research workflow (discover -> select -> process -> synthesize)
- Source registry for available providers
- Output format (summary + report + knowledge + GTD)
- Depth levels (quick/standard/deep)

### Quick Reference

| Question | Answer |
|----------|--------|
| Entry point? | `/research <topic\|url>` |
| Orchestrator? | `research-orchestrator` |
| Source registry? | `.datacore/registry/sources.yaml` |
| Settings? | `.datacore/settings.yaml` (`research.*`) |
| Output locations? | `content/reports/`, `content/summaries/`, `3-knowledge/` |
| What DIPs govern this? | DIP-0021, DIP-0004, DIP-0009 |

### Agents This Command Invokes

| Agent | Purpose |
|-------|---------|
| `research-orchestrator` | Full pipeline orchestration |
| `knowledge-extractor` | Per-source content processing (spawned by orchestrator) |
| `research-synthesizer` | Multi-source synthesis (spawned by orchestrator) |
| `podcast-creator` | Audio generation (optional, spawned by orchestrator) |

### Integration Points

- **DIP-0021** - Research architecture
- **DIP-0004** - Datacortex for discovery and dedup
- **DIP-0009** - GTD action item routing
- **Source Registry** - Available research sources

---

Deep multi-source research: discover, gather, process, synthesize, and optionally generate audio.

## Usage

```
/research <topic>
/research <url>
/research --topic "..." --depth quick|standard|deep
/research --podcast --space <space>
```

**Arguments:**

| Argument | Description |
|----------|-------------|
| `<topic>` | Free-text research query (triggers discovery phase) |
| `<url>` | Specific URL to process (skips discovery) |
| `--depth` | `quick` (Perplexity only), `standard` (default), `deep` (all sources + Gemini) |
| `--podcast` | Generate audio overview when done |
| `--space` | Target space for outputs (default: 0-personal) |

## Workflow

### Topic Research (discovery mode)

When given a topic:
1. **Discover** — fan out to all configured sources with `layers: [research]`
2. **Present** — show discovered sources with dedup report, user selects
3. **Process** — spawn `knowledge-extractor` per selected source
4. **Synthesize** — spawn `research-synthesizer` with all KE outputs
5. **Audio** (optional) — spawn `podcast-creator` if `--podcast` flag
6. **Present** — show research output inline

### URL Research (direct mode)

When given URL(s):
1. Skip discovery
2. Process URL(s) through `knowledge-extractor`
3. Synthesize if multiple URLs
4. Present results

## Output

Every research run produces (per DIP-0021 Section 3.5):

1. **Inline summary** — key findings, knowledge created, suggested actions
2. **Detailed report** — `content/reports/YYYY-MM-DD-[topic]-report.md`
3. **Knowledge artifacts** — literature notes + zettels in `3-knowledge/`
4. **GTD integration** — action items routed to org

## Examples

```
/research rapamycin dosing protocols for longevity
/research SOL market sentiment and whale accumulation
/research --topic "data tokenization competitors" --depth deep
/research https://example.com/paper.pdf
/research --topic "AI agent frameworks" --podcast
```

## Depth Levels

| Depth | Sources | Synthesis | Time |
|-------|---------|-----------|------|
| `quick` | Perplexity only | Inline answer | ~30s |
| `standard` | All configured | Full report | ~5min |
| `deep` | All + Gemini synthesis | Comprehensive report | ~15min |

## Agent

Invokes `research-orchestrator` which coordinates the full pipeline.

## Reference

See [DIP-0021: Search & Research Architecture](../dips/DIP-0021-search-research-architecture.md) for full specification.
