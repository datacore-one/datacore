---
name: opensource
description: Prepare a project or module for open-source release. 3-phase pipeline - strip secrets, sanitize, package with docs.
user_invocable: true
recall:
  # DIP-0029 default — engrams scoped to this command + tag-matched.
  scopes:
    - command:opensource
  tags:
    - opensource
---

# /opensource Command

Prepare any Datacore module, project, or codebase for public open-source release.

## Usage

```
/opensource <path>              # Release a specific project
/opensource .datacore/modules/X # Release a module
/opensource --check <path>      # Dry-run sanitization check only
```

## Workflow

### Phase 1: Assessment
1. Read the target project/module
2. Identify: license, existing README, dependencies, secrets risk areas
3. Present release plan to user for approval

### Phase 2: Execute Pipeline
Spawn the `opensource-pipeline` agent with the approved plan:
1. **Fork & Strip** — Copy to clean directory, remove secrets/private paths
2. **Sanitize & Verify** — Scan for leaked credentials, PII, internal URLs
3. **Package** — Generate README, LICENSE, CONTRIBUTING, CLAUDE.md, GitHub templates

### Phase 3: Publish
1. Create GitHub repo (if doesn't exist) via `gh repo create`
2. Push sanitized code
3. Create initial release tag
4. Update competitive landscape doc if relevant

## Key Rules
- NEVER skip the sanitization scan
- Present the scan report to user before publishing
- Default license: MIT (unless project specifies otherwise)
- Preserve existing docs — enhance, don't replace
- For Datacore modules, follow DIP-0001 contribution model

## Agent
Orchestrator: main conversation
Worker: `opensource-pipeline` agent (spawned for phases 2-3)
