---
summary: "CI/CD deployment with production verification via Chrome"
triggers: ["deploy project", "deploy status", "push and verify"]
context: on_match
---

# Dev Module

## Purpose

Full deployment workflow from pre-flight checks through CI/CD monitoring to production verification. Encodes the discipline that deploy is not done until verified in production — unit tests are necessary but not sufficient.

## Quick Start
> Say "deploy fairdrop" or run `/deploy [project]` to push, monitor CI/CD, and verify in production.

## How It Works

### `/deploy [project]`
1. **Pre-flight** — git status, correct remote/branch, type check, tests
2. **Push** — to configured remote (fork-aware, confirms unless `auto_push: true`)
3. **Monitor CI/CD** — poll GitHub Actions until complete
4. **Verify** — execute steps from project's `deploy.yaml` (curl, chrome, script)
5. **Summary** — result with timestamp, commit, environment

### `/deploy-status [project]`
Quick CI/CD status check without deploying.

### Verification Types
- **curl** — HTTP request with expected status/JSON
- **chrome** — browser automation via Claude-in-Chrome MCP (navigate, click, check network, assert)
- **script** — custom shell command

## Agents & Commands

| Name | Type | When to use |
|------|------|-------------|
| `/deploy` | command | Full deploy + verify workflow |
| `deploy-status` | skill | Quick CI/CD status check |

## Key Paths

| Path | Purpose |
|------|---------|
| `[project]/deploy.yaml` | Per-project deploy config |

## Setup

Each project needs a `deploy.yaml` in its root defining `gh_repo`, `git` (remote, branch), `preflight`, `pipeline`, and `verify` steps. See the deploy.yaml schema in the command definition for full reference.

## Boundaries
- Does not manage infrastructure or provisioning
- Does not create deploy.yaml — projects own their config
- Two Chrome MCP sessions cannot coexist

---

*This file covers structure, capability, and stable configuration. Learned behavior, user corrections, and operational preferences live as engrams — call `plur_recall_hybrid` for those.*
