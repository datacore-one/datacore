# Roadmap

Current status and planned improvements for Datacore.

## Current Release: v1.0.0

Core system operational with 21 modules, 147 agents, and 18 design specifications (DIPs).

See [CHANGELOG.md](CHANGELOG.md) for full v1.0.0 feature list.

## Next: v1.1.0

### Module Ecosystem
- [ ] Automated module discovery registry (DIP-0022)
- [ ] Module starter pack auto-import
- [ ] Workflow YAML executor for declarative automation (DIP-0022)

### Agent Intelligence
- [ ] Agent-to-agent contracts for formal coordination (DIP-0016)
- [ ] External agent compatibility: Google A2A protocol (DIP-0016)
- [ ] Semantic agent discovery via skill embeddings (DIP-0016)

### Learning Network
- [ ] Skills-as-exchange automation (DIP-0019)
- [ ] Engram exchange network over HTTP (DIP-0019)

### Research
- [ ] Per-source budget caps with auto-disable (DIP-0021)
- [ ] Source rotation strategies (DIP-0021)

## Future

### Scalability
- [ ] Multi-agent consensus for complex decisions (DIP-0016)
- [ ] Parallel nightshift task execution (DIP-0011)
- [ ] Event-driven task reactions (DIP-0009)

### Distribution
- [ ] Standalone MCP package for Claude Code users
- [ ] Portable secrets repository for multi-machine setups (DIP-0018)

### Community
- [ ] Public space template (datacore-org) for organizations
- [ ] Module marketplace
- [ ] Contributor rewards system (DIP-0001)

## Deferred (with rationale)

| Item | Rationale |
|------|-----------|
| Daemon mode for nightshift | Timer-based scheduling sufficient |
| GPG encryption for credentials | OS-level encryption adequate |
| Atomic write-back for engrams | No data loss observed |
| Delivery/publish outbox routing | Handled by comms module |

## Contributing

Significant changes follow the [DIP process](/.datacore/dips/README.md). For smaller improvements, open a PR directly.

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.
