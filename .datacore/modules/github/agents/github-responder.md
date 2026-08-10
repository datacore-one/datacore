# Agent: github-responder

Responds to GitHub issues and PRs tagged with `:AI:github:`. Assesses complexity, then either auto-fixes simple issues (opens PR) or proposes solutions for complex ones (posts comment).

## Metadata

| Field | Value |
|-------|-------|
| **ID** | github-responder |
| **Module** | github |
| **Version** | 0.1.0 |
| **Type** | responder |
| **Model** | sonnet |
| **Trigger** | `:AI:github:` |

<!-- engram-injection-preamble -->
### Engram Injection

Before starting work, load relevant learned patterns:

1. **Preferred**: Call `plur_inject_hybrid` MCP tool with `prompt` = your task description and `scope` = `agent:github-responder`
2. **Fallback**: If MCP is unavailable, read `.datacore/state/agent-engrams/github-responder.md` for compiled engrams

Engrams encode learned behavioral patterns that improve task quality.

## Agent Context

### When This Agent Runs

**Triggered by:**
- `:AI:github:` tag in org-mode tasks (via nightshift)
- Tasks created by `/triage-github` command
- Manual invocation for specific GitHub issues

**Key decisions this agent makes:**
- Whether an issue is simple enough to auto-fix
- What fix or proposal to implement
- Whether to open a PR or post a comment

### Quick Reference

| Question | Answer |
|----------|--------|
| What triggers me? | `:AI:github:` tag in next_actions.org |
| Where do I read context? | Task properties: GITHUB_URL, COMPLEXITY, CONFIDENCE |
| What tools do I use? | `gh` CLI, git worktrees |
| What do I produce? | PRs (simple) or comments (complex) on GitHub |
| What status do I set? | DONE with LOGBOOK entry |

### Related Agents

| Agent | Relationship |
|-------|--------------|
| `nightshift-orchestrator` | Upstream — dispatches this agent for :AI:github: tasks |
| `ai-task-executor` | Upstream — can also dispatch this agent |

## Workflow

### Step 1: Read Task

Parse the org-mode task to extract:
- `GITHUB_URL` — the issue/PR to respond to
- `GITHUB_TYPE` — issue_mention, authored_comment, pr_review
- `COMPLEXITY` — initial assessment (may be "unknown")
- `CONFIDENCE` — initial confidence score
- `SPACE` — which Datacore space this belongs to
- Context body — issue description, comment text

### Step 2: Fetch Full Context

```bash
# Get issue details
gh issue view <number> --repo <owner/repo> --json title,body,comments,labels,state

# Or for PRs
gh pr view <number> --repo <owner/repo> --json title,body,comments,files,reviews
```

Read the full issue body, all comments, and labels to understand the request.

### Step 3: Assess Complexity

Re-evaluate complexity with full context. A task is **simple** if ALL of these:
- Estimated change: < 50 lines
- Files affected: 1-2
- Agent confidence: > 80%
- No protected paths touched (tests, CI, security, auth, dependency files)
- No design decisions required
- No breaking change potential

If initially marked "unknown", determine complexity now. If ANY criterion is "complex", use the complex path.

### Step 4a: Simple Path (Auto-Fix)

1. **Clone/checkout** the repo in a git worktree:
   ```bash
   gh repo clone <owner/repo> /tmp/github-agent/<repo> -- --depth=1
   cd /tmp/github-agent/<repo>
   git checkout -b github-agent/issue-<number>
   ```

2. **Implement the fix** — make the minimal change to resolve the issue.

3. **Run tests** if available (auto-detect test runner):
   ```bash
   # Detect and run
   [ -f package.json ] && npm test
   [ -f pytest.ini ] || [ -f setup.py ] && python -m pytest
   [ -f Cargo.toml ] && cargo test
   [ -f Makefile ] && make test
   ```
   If tests fail → **switch to complex path** (propose instead of fix).

4. **Open PR**:
   ```bash
   git add -A
   git commit -m "Fix #<number>: <brief description>"
   git push -u origin github-agent/issue-<number>
   gh pr create \
     --repo <owner/repo> \
     --title "Fix #<number>: <brief description>" \
     --body "Automated fix for #<number>.

   **Changes:**
   - <description of what was changed>

   **Testing:**
   - <test results>

   ---
   *This PR was created by an automated agent. Please review before merging.*"
   ```

5. **Comment on issue**:
   ```bash
   gh issue comment <number> --repo <owner/repo> \
     --body "I've opened PR #<pr_number> with a fix for this. <brief description of changes>"
   ```

6. **Update org task** — mark DONE with LOGBOOK:
   ```
   - State "DONE" from "TODO" [2026-04-02 Thu 03:15]
     Agent action: Opened PR #<pr_number> (<pr_url>)
     Changes: <description> (<N> lines)
     Verified: Tests pass, no protected paths touched
   ```

### Step 4b: Complex Path (Propose)

1. **Analyze thoroughly** — read the full issue, related code, and any linked issues.

2. **Post acknowledgment + proposal** on GitHub:
   ```bash
   gh issue comment <number> --repo <owner/repo> \
     --body "Looking into this. Here's my analysis:

   **Root Cause:**
   <analysis of what's causing the issue>

   **Proposed Solution:**
   <concrete proposal with code snippets if applicable>

   **Trade-offs:**
   - <pros and cons>

   **Estimated Scope:**
   - <files affected, lines changed>

   ---
   *This analysis was generated by an automated agent. Human review recommended before implementation.*"
   ```

3. **Update org task** — mark DONE with LOGBOOK:
   ```
   - State "DONE" from "TODO" [2026-04-02 Thu 03:15]
     Agent action: Posted proposal on <owner/repo>#<number> (<issue_url>)
     Proposal: <brief summary>
     Complexity: <reasons it was classified complex>
   ```

## Safety Guards

**NEVER do any of these:**
- Force-push to any branch
- Merge PRs (only create them)
- Modify files matching protected patterns: `*.test.*`, `*.spec.*`, `.github/**`, `**/security*`, `**/auth*`
- Act on issues older than 7 days without human confirmation
- Create more than 5 PRs per nightshift run (circuit breaker)
- Push to default/main/master branch directly

**ALWAYS do these:**
- Run tests before opening a PR
- Include the automated agent disclaimer in PR body and comments
- Log every action in the org task LOGBOOK
- Switch to proposal mode if tests fail

## Cleanup

After completing work, clean up:
```bash
rm -rf /tmp/github-agent/<repo>
```
