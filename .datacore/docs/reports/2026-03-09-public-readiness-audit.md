# Datacore Public Readiness Audit

**Date**: 2026-03-09
**Scope**: Complete repository audit for open-source publication
**Method**: Automated scanning of all 326 tracked files + git history analysis
**Prior audit**: 2026-03-04 (scored 0.91 avg, claimed all issues fixed)

---

## Audit Areas

| # | Area | Description |
|---|------|-------------|
| A1 | Secrets & Credentials | API keys, tokens, passwords in tracked files and git history |
| A2 | Personal Information (PII) | Names, emails, personal paths, usernames |
| A3 | Business-Sensitive Content | Strategy docs, pricing, competitor intel, fundraising |
| A4 | Git History Hygiene | Secrets or PII recoverable from past commits |
| A5 | Privacy Architecture | Gitignore coverage, layered context compliance (DIP-0002) |
| A6 | Legal & License | LICENSE, attribution, third-party code |
| A7 | Documentation Readiness | README, INSTALL, CONTRIBUTING, SECURITY for public audience |
| A8 | Staged/Uncommitted Files | Files about to be committed that shouldn't be |

---

## Executive Summary

**The repository is NOT ready for public publication.** The prior audit (2026-03-04) scored 0.91 and claimed all blockers were fixed. This re-audit found:

- **1 leaked API key** (xAI) in both current files AND git history
- **12+ PII exposures** across tracked files (personal emails, full name, server paths)
- **6 business-critical documents** that reveal corporate structure, fundraising strategy, competitor intelligence, and regulatory positioning
- **5 draft files** staged for commit with hardcoded personal paths and engagement targeting data

The prior audit's claim that "all 7 `docs/plans/` files scrubbed of PII" is false — multiple files still contain personal identifiers. The `.mailmap` was noted as "Fixed" but it still contains the personal emails it was meant to hide.

---

## A1: Secrets & Credentials

### CRITICAL

| Finding | File | Lines |
|---------|------|-------|
| Real xAI API key (`xai-pUdW...`) | `docs/plans/2026-02-24-engagement-engine-plan.md` | 1371, 1428 |

**Key format**: `xai-[79 chars]` — full production API key for x.ai/Grok.
**In git history**: Yes, committed in `f27407a` ("Sync: 2026-02-24"), pushed to origin.
**Action**: Rotate key immediately at https://console.x.ai. Key remains recoverable even after file edit.

### SAFE (false positives)

- `sk-abc123...` in contribution-pipeline-plan.md — intentional test fixture
- `ghp_123456...` in same file — test fixture for secret scanner
- `test_secret` in forge-implementation-plan.md — test fixture

---

## A2: Personal Information (PII)

### CRITICAL — Must fix

| Finding | File | Detail |
|---------|------|--------|
| Full legal name + 2 personal emails | `.mailmap` | `[REDACTED NAME] <[REDACTED]@[REDACTED]>`, `[REDACTED]@[REDACTED]`, `[USERNAME]` username, `[HOSTNAME]` hostname |
| Personal paths + engagement targets | `draft_c2*.py` (5 files) | `~/Data/...`, real X handles being targeted |
| Personal paths + medical/personal tasks | `org-workspace/scripts/process_inbox.py` | `~/Data/0-personal/org`, gastroenterology appointment, personal tasks |

### HIGH — Should fix

| Finding | File | Detail |
|---------|------|--------|
| Hardcoded server username | `.datacore/lib/server_health_check.py` | `~/Data` (3 occurrences) |
| Hardcoded server username | `.datacore/lib/server_health_check.sh` | `~/Data` |
| Personal paths in docs | `.datacore/lib/README-server-health-check.md` | 3x `~/` |
| Personal paths in docs | `.datacore/lib/server_health_check.README.md` | 5x `~/` |
| Username in code examples | `.datacore/plans/2026-02-19-datacore-mcp-*.md` (3 files) | `[USERNAME]/personal`, `[USERNAME]-architecture-v1` |
| Username in example JSON | `docs/plans/2026-02-28-engagement-gamification-spec.md` | `display_name: "[USERNAME]"` |
| Server paths + contact network | `content/reports/2026-02-22-crm-proof-of-concept-report.md` | `~/Data/`, 992 contacts detail |
| Real contact + personal relationship | `.datacore/dips/DIP-0020-whatsapp-module.md` | [REDACTED NAME] + conference meeting reference |
| Username in test | `.datacore/lib/test_pr_review.py` | `pr_author='[USERNAME]'` |
| Username in deploy cmd | `docs/plans/2026-03-04-fairdrop-campaign-infra.md` | `sed "s/{{USERNAME}}/[USERNAME]/g"` |
| Full name + corporate entity | `docs/plans/2026-03-08-katra-design.md` | "[NAME] + co-founders (natural persons)" |
| Author attribution | `.datacore/plans/2026-02-19-eip-agent-knowledge-registry.md` | "Author: [NAME] (Fair Data Society)" |

---

## A3: Business-Sensitive Content

### CRITICAL — Must remove before publication

| File | Content Type | Risk |
|------|-------------|------|
| `.datacore/plans/2026-02-19-datacore-gtm-design.md` | Full GTM strategy, pricing, competitor analysis, token economics | Reveals complete business playbook |
| `docs/plans/2026-03-08-katra-design.md` | Corporate structure (BVI), VASP avoidance strategy, entity relationships | Regulatory strategy exposure |
| `content/emails/2026-02-21-investor-email-sequences.md` | Investor outreach templates, check sizes, segmentation | Active fundraising strategy |
| `content/emails/2026-02-22-investor-sequences-automated.md` | Automated fundraising sequences | Same as above |
| `content/reports/2026-02-22-crm-proof-of-concept-report.md` | Real contact network, geographic focus, relationship pipeline | Business intelligence |
| `docs/reports/2026-03-04-publication-readiness-audit.md` | Past vulnerabilities, fix details, attack surface hints | Security hygiene |

### HIGH — Should sanitize or remove

| File | Content Type | Risk |
|------|-------------|------|
| `content/playbook/2026-02-22-email-closing-playbook.md` | "Datafund" named in strategies | Ties content to specific business |
| `docs/plans/2026-03-04-datacore-launch-design.md` | Token design, C&D contingency, DAO trigger | Strategic IP |
| `docs/plans/2026-02-22-forge-autonomous-business-design.md` | Competitor financials, unit economics, ToS risk | Competitive intelligence |
| `4-forge/dashboard.md` | Product pricing, pipeline, competitor data | Operational business data |
| `docs/plans/2026-03-04-fairdrop-campaign-infra.md` | Campaign automation strategy, rate limits | Marketing IP |
| `.datacore/modules/comms/config/anchor-accounts.yaml` | ~35 X accounts with engagement strategy notes | Reveals monitoring targets |
| `datacore.lock.yaml` | Private team repo URLs | Organizational structure |

---

## A4: Git History Hygiene

| Finding | Severity | Detail |
|---------|----------|--------|
| xAI API key in history | CRITICAL | In commits `f27407a` and `3ee15d8`, pushed to origin/main |
| Personal emails in commit authors | HIGH | `[REDACTED]@[REDACTED]`, `[REDACTED]@[REDACTED]` in all historical commits |
| `credential-index.yaml` in history | LOW | Was tracked then removed in `f3b86eb`; contains metadata only, no secrets |

**Note**: `.mailmap` only affects `git log` display. Raw git objects (`git log --no-mailmap`, `git cat-file`) still expose original author emails. Full history rewrite with `git filter-repo` needed for complete remediation.

---

## A5: Privacy Architecture

| Check | Status | Notes |
|-------|--------|-------|
| `.gitignore` coverage | PASS | Comprehensive: spaces, .env, .local.md, org files, journals, secrets all excluded |
| DIP-0002 layered context | PASS | .base.md/.local.md pattern correctly implemented |
| Pre-commit hooks | PARTIAL | Scans .base.md + staged .md for PII, but missed xAI key in plan docs |
| Spaces isolation | PASS | All `[0-9]-*/` directories gitignored |
| Secrets directory | PASS | `.datacore/env/` gitignored, only `.env.example` tracked |
| install.yaml | PASS | Gitignored, `.example` templates provided |

**Gap**: The pre-commit hook scans for PII patterns but the xAI key bypassed it because it was embedded inline in a markdown planning document (not a `.env` file or `.base.md` file). The hook should scan ALL staged files for API key patterns.

---

## A6: Legal & License

| Check | Status | Notes |
|-------|--------|-------|
| LICENSE file | PASS | MIT License, copyright "Datacore" (no personal name) |
| CODEOWNERS | PASS | Uses `@datacore-one/maintainers` team (no personal handles) |
| CODE_OF_CONDUCT | PASS | Standard Contributor Covenant |
| SECURITY.md | PASS | Uses `security@datacore.one` |
| Third-party attribution | PASS | No vendored third-party code found |
| DIP submodule | NOTE | Separate repo (`datacore-one/datacore-dips`), already public |

---

## A7: Documentation Readiness

| Check | Status | Notes |
|-------|--------|-------|
| README.md | PASS | MCP-first positioning, clear value prop |
| INSTALL.md | PASS | Detailed setup instructions |
| CONTRIBUTING.md | PASS | Fork-and-overlay workflow documented |
| GETTING_STARTED.md | PASS | Quick start guide |
| CHANGELOG.md | PASS | Version history present |
| ROADMAP.md | PASS | Public roadmap |

---

## A8: Staged/Uncommitted Files

| File | Status | Issue |
|------|--------|-------|
| `draft_c2.py` through `draft_c2e.py` | Staged (A) | Personal paths, real X handles, engagement targeting scripts |
| `org-workspace/` | Untracked (?) | Contains `process_inbox.py` with personal paths and medical references |
| `.datacore/engagement/profile.yaml` | Modified (MM) | Engagement profile — review before commit |

**Action**: `git rm --cached draft_c2*.py` and add to `.gitignore`. Do NOT commit `org-workspace/`.

---

## Remediation Plan

### Phase 1: Immediate (before any publication)

1. **Rotate xAI API key** at https://console.x.ai
2. **`git rm` the following from tracking**:
   - `draft_c2*.py` (5 files)
   - `.datacore/plans/2026-02-19-datacore-gtm-design.md`
   - `content/emails/2026-02-21-investor-email-sequences.md`
   - `content/emails/2026-02-22-investor-sequences-automated.md`
   - `content/playbook/2026-02-22-email-closing-playbook.md`
   - `content/reports/2026-02-22-crm-proof-of-concept-report.md`
   - `docs/reports/2026-03-04-publication-readiness-audit.md`
   - `docs/plans/2026-03-08-katra-design.md`
3. **Scrub PII from remaining tracked files** (replace `[USERNAME]` with generic examples, replace `~/` with `$HOME/` or `{{DATACORE_ROOT}}`)
4. **Fix `.mailmap`** — remove personal emails or replace with noreply addresses
5. **Add `org-workspace/` to `.gitignore`**

### Phase 2: Git History Cleanup

6. **Rewrite git history** using `git filter-repo` to:
   - Remove the xAI API key string from all commits
   - Remove the files listed in Phase 1 from all commits
   - Optionally rewrite author emails to `dev@datacore.one`
7. **Force push** to origin (all collaborators must re-clone)

### Phase 3: Business Content Review

8. **Review and sanitize** remaining plan docs (launch, forge, fairdrop) — strip competitor intelligence, unit economics, token strategy sections
9. **Decide on `datacore.lock.yaml`** — remove private team repo URLs or accept exposure
10. **Review `anchor-accounts.yaml`** — decide if engagement targeting list should be public

### Phase 4: Prevention

11. **Enhance pre-commit hook** to scan ALL staged files (not just .md) for API key patterns
12. **Add `.datacore/plans/` to selective gitignore** or create a `.plans.local/` pattern for sensitive plans
13. **Document the boundary** between public plans and private strategy docs

---

## Scoring

### By Audit Area

| Area | Score | Rationale |
|------|-------|-----------|
| A1: Secrets | 0.30 | Active API key leaked in tracked file AND git history |
| A2: PII | 0.40 | 12+ exposures across multiple files, prior audit claimed fixed |
| A3: Business Content | 0.20 | 6 critically sensitive documents tracked, would expose full business strategy |
| A4: Git History | 0.35 | API key and personal emails permanently in history |
| A5: Privacy Architecture | 0.85 | Strong gitignore and DIP-0002, but pre-commit gap let key through |
| A6: Legal & License | 0.95 | Clean MIT license, proper attribution |
| A7: Documentation | 0.90 | Complete public-facing docs suite |
| A8: Staged Files | 0.30 | 5 personal scripts staged for commit |

### Overall Score: 0.53

**Verdict: NOT READY for public publication.**

The privacy architecture (gitignore, DIP-0002) and documentation are strong. The blockers are:
1. A leaked production API key requiring rotation + history rewrite
2. Extensive PII that was previously flagged but not actually remediated
3. Business strategy documents that would expose the complete commercial playbook

After Phase 1+2 remediation, the score would rise to approximately 0.85. After Phase 3, approximately 0.95.

---

## Comparison with Prior Audit (2026-03-04)

| Claim in prior audit | This audit's finding |
|---------------------|---------------------|
| "All P1 blockers fixed" | 1 CRITICAL secret still present, 12+ PII exposures remain |
| "All 7 docs/plans/ files scrubbed of PII" | Multiple files still contain `[USERNAME]` username and personal paths |
| ".mailmap maps personal email - Fixed" | .mailmap still contains personal emails (it hides them in log display but file itself is readable) |
| "API keys in tracked files: None found" | xAI API key present in `docs/plans/2026-02-24-engagement-engine-plan.md` |
| "Score: 0.91" | Current score: 0.53 |

The prior audit appears to have verified fixes in some files but missed others, and did not scan for API keys embedded in planning documents. The `.mailmap` "fix" only addresses display, not actual data exposure.

---

## Evaluator Panel Consensus (Round 1)

### Scores by Area

| Area | Audit | Security | CTO | CEO | Avg |
|------|-------|----------|-----|-----|-----|
| A1: Secrets | 0.30 | 0.25 | 0.25 | 0.25 | 0.26 |
| A2: PII | 0.40 | 0.35 | 0.35 | 0.35 | 0.36 |
| A3: Business Content | 0.20 | 0.20 | 0.20 | 0.10 | 0.18 |
| A4: Git History | 0.35 | 0.20 | 0.35 | 0.30 | 0.30 |
| A5: Privacy Architecture | 0.85 | 0.70 | 0.70 | 0.80 | 0.76 |
| A6: Legal & License | 0.95 | 0.90 | 0.90 | 0.90 | 0.91 |
| A7: Documentation | 0.90 | 0.90 | 0.82 | 0.85 | 0.87 |
| A8: Staged Files | 0.30 | 0.20 | 0.15 | 0.20 | 0.21 |
| **Overall** | **0.53** | **0.46** | **0.47** | **0.47** | **0.48** |

### Evaluator Verdicts

All three evaluators independently reached the same verdict: **NOT READY for public publication.**

### Additional Findings from Evaluators

The Security, CTO, and CEO evaluators identified findings the initial audit missed:

| # | Finding | Source | Severity |
|---|---------|--------|----------|
| E1 | Third-party contributor email (`[REDACTED]@[REDACTED]`) in 3 commits | Security | HIGH — privacy consent issue |
| E2 | `.mailmap` is itself a PII leak (tracked file containing personal emails) | Security | HIGH — self-defeating fix |
| E3 | `profile.yaml` contains behavioral metadata, internal product names, on-chain identity prep | Security | HIGH — should be gitignored |
| E4 | Broken submodule: `.datacore/dips` is gitlink with no `.gitmodules` file | CTO | CRITICAL — clone fails |
| E5 | Pre-commit hook cannot catch pre-existing secrets (only scans diffs) | Security/CTO | ARCHITECTURAL — requires full-repo scan tool |
| E6 | Comms module is operational (not generic) — `anchor-accounts.yaml` is live targeting list | CTO | HIGH — not community-ready |
| E7 | `content/` and `docs/plans/` are commercial workspace, not open-source content | CEO | CRITICAL — strategic boundary not defined |
| E8 | GTM doc alone is disqualifying — complete competitive playbook with named competitors | CEO | CRITICAL |
| E9 | Investor templates in public repo = fundraising strategy exposed to recipients | CEO | HIGH |
| E10 | ROADMAP.md needs review for commercial strategy references after cleanup | CEO | MEDIUM |

### Consensus Areas (all evaluators agree)

1. **xAI API key must be rotated immediately** — all score A1 at 0.25
2. **Git history rewrite is mandatory**, not optional — all agree filter-repo is Phase 1
3. **Business strategy documents must be removed** — GTM, Katra, investor emails, CRM report
4. **`.mailmap` approach is insufficient** — it exposes what it tries to hide
5. **`draft_c2*.py` must be unstaged and gitignored** — engagement targeting in public repo
6. **The repo needs a strategic boundary decision**: what belongs in public vs. private

### Disagreement Areas

| Area | Range | Source of disagreement |
|------|-------|----------------------|
| A3: Business Content | 0.10-0.20 | CEO rates lower due to competitive intelligence exposure |
| A4: Git History | 0.20-0.35 | Security rates lower due to 328 commits with personal email |
| A5: Privacy Architecture | 0.70-0.85 | CTO/Security rate lower due to broken submodule and hook gaps |

### Strategic Recommendation (CEO)

> "The repo needs two separate remediation tracks. Track 1 (Security/PII) is 1-2 days of work. Track 2 requires a decision: decide what this repo IS. If it's a public open-source project, then `content/`, `docs/plans/`, `4-forge/`, and `.datacore/plans/` should not be tracked. These belong in a separate private repo."

---

## Hard Blockers (unanimous)

These must ALL be resolved before publication:

1. **Rotate xAI API key** at https://console.x.ai
2. **Rewrite git history** with `git filter-repo` to remove:
   - The xAI API key string
   - All files being removed from tracking
   - Optionally: rewrite author emails to `dev@datacore.one`
3. **Remove from tracking** (`git rm`):
   - `draft_c2*.py` (5 files)
   - `.datacore/plans/2026-02-19-datacore-gtm-design.md`
   - `content/emails/` (both files)
   - `content/playbook/2026-02-22-email-closing-playbook.md`
   - `content/reports/2026-02-22-crm-proof-of-concept-report.md`
   - `docs/reports/2026-03-04-publication-readiness-audit.md`
   - `docs/plans/2026-03-08-katra-design.md`
4. **Fix broken submodule**: restore `.gitmodules` or flatten DIPs into tree
5. **Fix `.mailmap`**: gitignore it, or replace personal emails with noreply addresses
6. **Add to `.gitignore`**: `org-workspace/`, `draft_*.py`, `content/emails/`, `content/playbook/`
7. **Scrub PII** from remaining tracked files (server_health_check.py, plan docs, test files)
8. **Gitignore `profile.yaml`** and provide example template instead

### Estimated effort: 2-3 focused days (Track 1) + architectural decision on content boundary (Track 2)

---

## Final Consensus (Round 2 — Converged)

A convergence moderator resolved all disagreements between evaluators.

### Consensus Scores

| Area | Consensus | Rationale |
|------|-----------|-----------|
| A1: Secrets | **0.25** | Production API key in tracked file and history |
| A2: PII | **0.35** | 12+ exposures, third-party email consent issue |
| A3: Business Content | **0.10** | Classification failure — strategic docs incompatible with public repo |
| A4: Git History | **0.20** | 328 commits with personal emails, third-party consent, API key in objects |
| A5: Privacy Architecture | **0.70** | Strong gitignore/DIP-0002, undermined by broken submodule and hook flaw |
| A6: Legal & License | **0.90** | Clean MIT, proper attribution |
| A7: Documentation | **0.87** | Complete docs suite |
| A8: Staged Files | **0.20** | 5 engagement scripts staged, medical PII in untracked dir |

### Overall Consensus Score: 0.45

### Key Resolutions

1. **History rewrite is Phase 1, not Phase 2** — file removal and history rewrite are a single atomic operation
2. **`.mailmap` must be gitignored**, not fixed — the file exposes what it tries to hide
3. **Broken submodule is CRITICAL** — a repo that fails to clone cannot be published
4. **Business content is a classification failure (0.10)**, not a file hygiene problem (0.20)

### Hard Blockers — Priority Order

1. **Rotate xAI API key** at https://console.x.ai (immediate)
2. **Fix broken submodule** — restore `.gitmodules` or flatten DIPs into tree
3. **Atomic history rewrite** with `git filter-repo`:
   - Remove xAI key string from all commits
   - Remove all files being untracked
   - Rewrite author emails to `dev@datacore.one`
   - Address `[REDACTED]@[REDACTED]` (consent or rewrite)
   - Force push (all collaborators re-clone)
4. **Remove business content from tracking** (`git rm` + gitignore):
   - `draft_c2*.py` (5 files)
   - `.datacore/plans/2026-02-19-datacore-gtm-design.md`
   - `docs/plans/2026-03-08-katra-design.md`
   - `content/emails/` (both files)
   - `content/playbook/2026-02-22-email-closing-playbook.md`
   - `content/reports/2026-02-22-crm-proof-of-concept-report.md`
   - `docs/reports/2026-03-04-publication-readiness-audit.md`
5. **Gitignore `.mailmap` and `profile.yaml`** — provide `.example` templates
6. **Scrub PII from remaining tracked files** (12+ files with `[USERNAME]` username/paths)
7. **Strategic boundary decision**: gitignore `content/`, `docs/plans/` wholesale, or individually review each

### Path to Publication

| After completing... | Projected score |
|--------------------|-|
| Blockers 1-2 (key rotation + submodule) | 0.55 |
| Blockers 3-5 (history rewrite + content removal) | 0.80 |
| Blockers 6-7 (PII scrub + boundary decision) | 0.92+ |

**Estimated effort**: 3-4 focused days once Blocker 7's decision is made. No partial publication possible — history rewrite requires all content decisions to be finalized before execution.
