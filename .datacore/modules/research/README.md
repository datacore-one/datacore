# Research Module

> Curate links. Wake up to podcasts and insights.

Automated research processing with NotebookLM podcast generation, literature notes, atomic zettels, and industry landscape building.

## Installation

```bash
# Clone to modules directory
git clone https://github.com/datacore-one/module-research .datacore/modules/research

# Or if already in a Datacore installation
cd ~/Data/.datacore/modules
git clone https://github.com/datacore-one/module-research research
```

## Requirements

- **Datacore** core installation
- **Nightshift module** for overnight processing
- **nlm CLI** for NotebookLM integration (optional but recommended)

### Installing nlm

```bash
go install github.com/tmc/nlm@latest
```

## Quick Start

1. **Add research links** to `0-personal/org/research_learning.org`:
   ```org
   * Project Alpha
   ** TODO [#B] Competitor Analysis Article
      Link: https://example.com/article
   ```

2. **Check status** with `/research-status`

3. **Process manually** with `/research-daily` or wait for nightshift

4. **Create ad-hoc podcasts** with `/create-podcast`

## Commands

| Command | Description |
|---------|-------------|
| `/research-status` | View queue, recent podcasts, stats |
| `/research-daily` | Manually trigger processing |
| `/create-podcast` | Create podcast from URLs |

## How It Works

```
research_learning.org (TODO items)
         │
         ▼
daily-research-processor (orchestrator)
         │
         ├──► Literature notes + Zettels
         ├──► Action items → next_actions.org
         ├──► CRM entities → contacts/
         ├──► Podcasts (daily + topical)
         └──► Industry landscape updates
```

## Output Locations

| Type | Path |
|------|------|
| Podcasts | `0-personal/content/podcasts/` |
| Literature Notes | `0-personal/notes/2-knowledge/literature/` |
| Zettels | `0-personal/notes/2-knowledge/zettel/` |
| Industry Landscape | `1-datafund/1-tracks/research/Industry landscape.md` |

## Configuration

Create `~/.datacore/settings.local.yaml`:

```yaml
research:
  # Podcast settings
  podcast_defaults:
    duration_target: "30min"
    max_sources: 10

  # Daily processing
  daily_processing:
    enabled: true
    max_links_per_night: 20

  # Action extraction
  action_extraction:
    enabled: true
    max_per_source: 5

  # Power user settings
  auto:
    skip_confirmations: false
    skip_source_warnings: false
```

## Focus Areas

Research is organized by focus area in `research_learning.org`:

- **Work:** Project Alpha, Organization, Datacore
- **Trading:** Markets, analysis
- **Personal:** Health, productivity, learning
- **General:** Technology, business, science

## Integration with Other Modules

### CRM Module
When installed, automatically extracts entities (people, companies, projects) from research and creates draft contacts.

### Nightshift Module
Processes research overnight, generates podcasts, updates morning briefing.

## Troubleshooting

### nlm not found
```bash
# Check if installed
which nlm

# Install if missing
go install github.com/tmc/nlm@latest

# Or configure path
# In settings.local.yaml:
research:
  nlm_path: "/path/to/nlm"
```

### Podcasts not generating
1. Check nlm is authenticated: `nlm list`
2. Check nightshift ran: `/nightshift-status`
3. Try manual: `/research-daily`

### URLs failing
- Check URL is publicly accessible
- Paywalled content won't work
- Try archive.org version

## License

MIT

## Links

- [Datacore](https://github.com/datacore-one/datacore)
- [nlm CLI](https://github.com/tmc/nlm)
- [NotebookLM](https://notebooklm.google.com)
