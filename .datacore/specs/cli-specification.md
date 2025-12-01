# Datacore CLI Specification

**Status:** Approved for implementation
**Consensus:** 0.87 (4 evaluators)
**Date:** 2026-02-06

## Overview

`@datacore/cli` is an npm-installable CLI for setting up and managing Datacore installations.

```bash
npm install -g @datacore/cli
```

## Design Principles

1. **CLI = Thin Wrapper** - CLI collects parameters and invokes agents; agents do semantic work
2. **Agent Delegation** - Claude Code is the execution runtime for agents
3. **Mechanical Only** - CLI does: git, config, sync, doctor. Agents do: routing, extraction, decisions
4. **Interactive + Headless** - Wizard for setup, flags for automation

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     CLI Layer (Mechanical)                  │
├─────────────────────────────────────────────────────────────┤
│  • Parameter collection (prompts)                           │
│  • Dependency checking (doctor)                             │
│  • Configuration management (settings.yaml)                 │
│  • Git operations (sync)                                    │
│  • Agent invocation (via Claude Code)                       │
│  • Progress reporting                                       │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼ invokeAgent()
┌─────────────────────────────────────────────────────────────┐
│                   Agent Layer (Semantic)                    │
├─────────────────────────────────────────────────────────────┤
│  • create-space agent      → Space structure decisions      │
│  • ingest-coordinator      → File routing, extraction       │
│  • gtd-inbox-processor     → Task processing                │
│  • context-maintainer      → CLAUDE.md generation           │
│  • module-registrar        → Module integration             │
└─────────────────────────────────────────────────────────────┘
```

## Commands

| Command | Purpose | Delegation |
|---------|---------|------------|
| `datacore init` | Setup wizard | create-space agent |
| `datacore doctor` | Check dependencies | Mechanical |
| `datacore space create` | Create space | create-space agent |
| `datacore space list` | List spaces | Mechanical |
| `datacore space audit` | Audit structure | structural-integrity agent |
| `datacore module install` | Install module | Mechanical + registry |
| `datacore module list` | List modules | Mechanical |
| `datacore module update` | Update modules | Mechanical |
| `datacore ingest <path>` | Import files | ingest-coordinator agent |
| `datacore sync` | Git pull all | Mechanical |
| `datacore sync push` | Git commit+push | Mechanical |
| `datacore sync status` | Git status | Mechanical |
| `datacore config show` | Show settings | Mechanical |
| `datacore config get` | Get setting | Mechanical |
| `datacore config set` | Set setting | Mechanical |
| `datacore context rebuild` | Regenerate CLAUDE.md | context_merge.py |
| `datacore context validate` | Check for leaks | context_merge.py |
| `datacore today` | Daily briefing | /today command |
| `datacore tomorrow` | End-of-day | /tomorrow command |
| `datacore gtd daily-start` | Morning planning | /gtd-daily-start |
| `datacore gtd daily-end` | Evening wrap-up | /gtd-daily-end |
| `datacore nightshift status` | Queue status | Mechanical |
| `datacore nightshift trigger` | Manual execution | Server API |
| `datacore cron install` | Setup automation | Platform-specific |
| `datacore recover` | Resume/rollback failed ops | State management |
| `datacore tour` | Interactive walkthrough | Built-in |
| `datacore docs` | Open documentation | Built-in |
| `datacore update` | Self-update | npm |

## Project Structure

```
datacore-cli/
├── package.json
├── tsconfig.json
├── src/
│   ├── index.ts              # Entry point
│   ├── routing.ts            # Command routing
│   ├── help.ts               # Help system
│   ├── format.ts             # JSON/human output
│   ├── errors.ts             # CLIError class
│   ├── config.ts             # Config management
│   ├── state.ts              # Operation state
│   ├── commands/
│   │   ├── init.ts
│   │   ├── doctor.ts
│   │   ├── space.ts
│   │   ├── module.ts
│   │   ├── ingest.ts
│   │   ├── sync.ts
│   │   ├── nightshift.ts
│   │   ├── config.ts
│   │   ├── context.ts
│   │   ├── gtd.ts
│   │   ├── cron.ts
│   │   ├── recover.ts
│   │   ├── tour.ts
│   │   └── update.ts
│   ├── lib/
│   │   ├── agent-invoker.ts
│   │   ├── git.ts
│   │   ├── platform.ts
│   │   ├── dependency.ts
│   │   ├── prompt.ts
│   │   └── paths.ts
│   └── types/
│       └── index.ts
└── tests/
```

## Key Interfaces

### CLIError

```typescript
class CLIError extends Error {
  code: string          // e.g., 'MISSING_CLAUDE'
  hint?: string         // e.g., 'Install: npm install -g ...'
  recoverable: boolean  // Can user retry?
  rollback?: () => Promise<void>
}
```

### OperationState

```typescript
interface OperationState {
  id: string
  operation: string
  status: 'pending' | 'in_progress' | 'completed' | 'failed'
  startedAt: string
  steps: { name: string; status: string; error?: string }[]
  rollback?: string[]
}
```

### AgentInvocation

```typescript
interface AgentInvocation {
  agent: string
  params: Record<string, unknown>
  stream?: boolean
}

interface AgentResult {
  success: boolean
  output: string
  artifacts?: Record<string, string>
  error?: string
}
```

## CLI/Agent Boundary

| Operation | CLI Does | Agent Does |
|-----------|----------|------------|
| Space create | Collect params, invoke agent | Structure decisions, git init, templates |
| Ingest | Validate path, invoke agent | 6-phase processing, routing, extraction |
| GTD commands | Invoke command | All GTD logic |
| Context | Call context_merge.py | (Python script handles) |
| Sync | Git operations | N/A (pure mechanical) |
| Doctor | Check dependencies | N/A (pure mechanical) |

## Distribution

```json
{
  "name": "@datacore/cli",
  "version": "1.0.0",
  "bin": {
    "datacore": "./dist/index.js"
  },
  "files": ["dist/"],
  "engines": {
    "node": ">=18"
  }
}
```

## Implementation Plan

See `.datacore/plans/datacore-cli-v1.md` for detailed implementation chunks.

## References

- DIP-0002: Layered Context Pattern
- DIP-0015: Semantic Organization (ingest workflow)
- DIP-0016: Agent Registry
- ade CLI: `~/Data/3-partnerspace/2-projects/ade/` (pattern reference)
