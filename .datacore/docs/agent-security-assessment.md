# Agent Security Assessment

**Date:** 2026-03-04
**Scope:** Datacore agent ecosystem security posture review

## Agent Categories by Access Level

### Category 1: Read-Only Agents (Low Risk)
- `tag-suggester`, `strategic-prioritizer`, `scaffolding-auditor`
- No web access, no file write beyond designated output paths
- Risk: Minimal

### Category 2: File Write Agents (Medium Risk)
- `session-learning`, `journal-entry-writer`, `context-maintainer`
- File write to designated paths only (learning/, journal/, CLAUDE.md)
- No web access
- Risk: Controlled — bounded write scope

### Category 3: Web + File Write Agents (Higher Risk)
- `knowledge-extractor`, `url-fetcher`, `research-orchestrator`
- Web access (WebFetch, Jina Reader) + file write (literature notes, zettels)
- Risk: Prompt injection via web content could influence file writes
- Mitigation: Content passes through extraction pipeline, not executed

### Category 4: Autonomous Execution (Highest Risk)
- `ai-task-executor` (nightshift)
- Full tool access: Bash, Read, Write, Edit, WebFetch
- Runs unattended overnight
- Risk: Broadest attack surface

## Security Boundaries

| Boundary | Status |
|----------|--------|
| Credential isolation | `.datacore/env/` gitignored, loaded per-agent |
| Nightshift isolation | Separate Claude instance on server, no shared state |
| Shell access | Via Bash tool only — no persistent shell, no root |
| Network access | WebFetch/Jina only — no raw socket access |
| File system scope | Working directory only (`~/Data/`) |
| Container isolation | **NOT IMPLEMENTED** — agents share host filesystem |
| Scoped network access | **NOT IMPLEMENTED** — no per-agent network policies |

## Risk Assessment

### Acceptable for Single-User
Current security posture is appropriate for single-user:
- All agents run under user's own permissions
- Credential isolation prevents accidental exposure in git
- Nightshift runs on dedicated server with limited scope
- No multi-tenant concerns

### Gaps for Multi-Tenant
Before multi-tenant deployment, address:
1. Container isolation — each agent in own container
2. Network policies — per-agent allowlists for web access
3. File system sandboxing — agents access only designated paths
4. Audit logging — track all agent file/web operations
5. Rate limiting — prevent runaway agents from excessive API calls

## Recommendations

- **Current priority:** None — acceptable for single-user
- **Pre-publication:** Document security model in README for transparency
- **Pre-multi-tenant:** Implement container isolation and network policies
