Perform a comprehensive **GitHub triage across all repositories relevant to this project/product**.

This is a **triage and review task**, not an implementation task.

Your goal is to answer:

> **What requires attention now, what is blocking the team or a release, which open PRs are ready or problematic, and what should we tackle first?**

Do not merely produce an inventory of GitHub objects.

Analyze the current state and turn it into an actionable prioritized work queue.

---

# SCOPE

Inspect all GitHub repositories materially relevant to the product.

Start by identifying the relevant repository set.

Include repositories that contain or materially affect:

* the core product;
* specifications;
* modules/packages;
* integrations;
* executors/workers;
* deployment/installation tooling;
* shared libraries;
* tests;
* documentation where it affects implementation;
* infrastructure/configuration;
* release tooling.

For Datacore specifically, include the relevant `datacore-one/*` repositories, including the specifications in:

`datacore-one/datacore-dips`

and discover additional repositories that are actually referenced by the product, open issues, PRs, specs, dependencies, installation, or active development.

Do not assume every repository in the organization is relevant.

Do not omit a repository merely because it has little recent activity if another active repository depends on it.

Build the relevant repository set from evidence.

---

# DETERMINE MY GITHUB IDENTITY

Determine the authenticated GitHub user associated with this work.

Use that identity to distinguish:

* issues assigned to me;
* PRs authored by me;
* PRs requesting my review;
* issues/PRs where I am mentioned;
* issues/PRs where someone is waiting on me;
* work owned by other people.

Do not guess my username.

---

# FRESHNESS

Use the **current live GitHub state**.

Inspect:

* open issues;
* open pull requests;
* draft PRs;
* review requests;
* CI/check status;
* merge conflicts;
* labels;
* milestones;
* linked issues;
* dependencies between issues/PRs;
* recent relevant comments/reviews;
* release branches/tags/milestones where applicable.

Do not rely on stale remembered state.

Paginate sufficiently to avoid silently missing older open work.

---

# 1. REPOSITORY MAP

For every relevant repository determine:

**Repository:**
**Purpose:**
**Relationship to product:**
**Active development:** YES / LOW / NO
**Open issues:**
**Open PRs:**
**Current release relevance:**
**Important dependency relationships:**

Identify:

* core repositories;
* supporting repositories;
* specification repositories;
* integration repositories;
* repositories that appear obsolete/superseded;
* repositories whose open work affects another repo.

Do not spend equal effort on irrelevant repositories.

---

# 2. OPEN ISSUE TRIAGE

Inspect all relevant open issues.

For each issue determine:

* what the issue actually requires;
* severity/impact;
* whether it is still valid;
* who owns it;
* whether it blocks someone;
* whether it blocks another issue/PR;
* whether it blocks a release;
* whether work has already been implemented elsewhere;
* whether a PR already addresses it;
* whether it duplicates another issue;
* whether it is underspecified;
* whether it should be split;
* whether it is waiting on a decision rather than code;
* whether it has become stale because architecture changed.

Do not trust labels blindly.

Compare labels/priority against the actual problem.

---

# ISSUES ASSIGNED TO ME

Create a dedicated assessment of everything currently assigned to me.

For each determine:

**Issue:**
**Repository:**
**Current state:**
**What remains:**
**Who/what is blocked:**
**Release impact:**
**Recommended next action:**
**Priority:** P0 / P1 / P2 / P3

Also identify issues that are **not technically assigned to me but effectively require my action**, for example:

* someone asked me a question;
* a decision is waiting on me;
* my review is required;
* my PR needs changes;
* another developer cannot continue without an architectural/product decision from me.

Call these:

> **WAITING ON ME**

This category is important.

---

# 3. OPEN PR TRIAGE

Inspect **every open PR in the relevant repository set**.

For each PR determine:

* purpose;
* linked issue/spec;
* author;
* draft/non-draft;
* age;
* CI/check state;
* mergeability;
* conflicts;
* review status;
* requested reviewers;
* unresolved review comments;
* dependencies on other PRs;
* whether another PR depends on it;
* release relevance;
* whether it has gone stale relative to `main`;
* whether the underlying issue still exists.

Classify each:

### READY TO MERGE

No known substantive blocker and required checks/reviews are satisfied.

### READY FOR REVIEW

Implementation appears complete but needs review.

### CHANGES REQUIRED

Substantive problem exists.

### BLOCKED

Another change, decision, environment, or dependency must land first.

### DRAFT / IN PROGRESS

Not intended for merge yet.

### STALE / RECONSIDER

The PR may no longer represent the correct approach.

### SUPERSEDED / CLOSE CANDIDATE

Another implementation or architectural change has replaced it.

Do not mark something `READY TO MERGE` solely because CI is green.

---

# 4. REVIEW OPEN PRs

Actually review the substantive open PRs.

Do not merely inspect GitHub metadata.

For every PR that is sufficiently complete to review:

1. read the PR description;
2. inspect the complete diff;
3. understand behavior before and after;
4. inspect surrounding code where necessary;
5. inspect relevant tests;
6. inspect linked issues/specifications;
7. check whether the implementation satisfies its stated objective.

Prioritize:

1. Security
2. Data preservation
3. Correctness
4. Compatibility
5. Concurrency/recovery
6. Tests
7. Maintainability
8. Style

Look for:

* security regressions;
* data loss/corruption;
* missing validation;
* incorrect authorization;
* concurrency/race problems;
* retry/idempotency problems;
* migration issues;
* compatibility breaks;
* incomplete implementations;
* error-path failures;
* missing tests;
* architectural duplication;
* divergence from relevant specifications.

For each reviewed PR report:

**Verdict:** APPROVE / CHANGES REQUIRED / BLOCKED / NEEDS DEEPER REVIEW

and list substantive findings.

Do not manufacture minor review comments to appear thorough.

---

# 5. REVIEW REQUESTS / WAITING ON MY REVIEW

Identify PRs specifically requesting my review.

Rank these highly when:

* a teammate cannot proceed until review;
* the PR is release-critical;
* another PR depends on it;
* the PR is otherwise merge-ready.

A five-minute review that unblocks another developer may deserve higher priority than a larger issue assigned to me.

Explicitly identify these opportunities.

---

# 6. TEAM BLOCKERS

Look beyond my assignments.

Determine where the **team is blocked**.

Examples:

* PR awaiting review;
* issue awaiting architectural decision;
* dependency PR that must merge before several others;
* failing shared CI;
* specification ambiguity;
* broken test infrastructure;
* release branch blocked by one issue;
* dependency/environment problem;
* incompatible parallel PRs;
* work duplicated by multiple people.

For each blocker identify:

**Blocker:**
**Who/what is blocked:**
**Required action:**
**Best owner:**
**Estimated unblock leverage:** HIGH / MEDIUM / LOW

Prioritize high-leverage work.

---

# 7. RELEASE READINESS

Determine whether an upcoming/current release can be identified from:

* milestones;
* version tags;
* release branches;
* release PRs;
* issue labels;
* roadmap/spec references;
* recent repository activity.

Do not invent a release deadline if none exists.

Identify:

### RELEASE BLOCKERS

Issues/PRs that should prevent release.

### RELEASE RISKS

Issues that may not formally block but create meaningful risk.

### POST-RELEASE

Work that does not need to delay the release.

Look specifically for:

* security/data-loss problems;
* failing CI;
* broken installation;
* migration problems;
* incompatible dependencies;
* unfinished API changes;
* specification/implementation divergence;
* missing upgrade paths;
* unverified deployment changes.

If the release appears safe after a small number of actions, identify the **minimum path to release**.

Example:

> Review PR A → merge PR A → rebase PR B → run installation checks → release.

---

# 8. DEPENDENCY / MERGE ORDER

Open work often has an implicit dependency graph.

Determine:

* which PR must merge first;
* which issue must be resolved first;
* which spec decision must happen first;
* which work can run independently;
* which branches are likely to conflict;
* where parallel work is safe.

Produce an execution graph where useful.

Example:

`Decision #X`
↓
`PR #Y`
↓
`PR #Z`
↓
`Release`

Identify **critical-path items**.

---

# 9. CONFLICTING / DUPLICATED WORK

Look for:

* multiple PRs solving the same problem;
* issues duplicated across repositories;
* old issue + newer issue describing the same defect;
* PR that implements behavior superseded by another architectural change;
* parallel changes likely to conflict;
* two specifications describing inconsistent behavior.

Flag them.

Recommend:

* merge;
* consolidate;
* close;
* supersede;
* sequence;
* resolve architecture first.

Do not let duplicate work consume team time unnecessarily.

---

# 10. STALE WORK

Review older open issues and PRs.

Do not classify something stale simply because of age.

Determine whether:

* it remains relevant;
* architecture changed;
* code already solved it;
* nobody owns it;
* it was abandoned;
* a linked dependency disappeared;
* it should become a current priority again.

Classify stale items as:

* STILL VALID
* NEEDS REFRESH
* SUPERSEDED
* CLOSE CANDIDATE
* NEEDS OWNER

---

# 11. SPECIFICATION / IMPLEMENTATION ALIGNMENT

For work affecting behavior defined by specifications, inspect the relevant DIPs in:

`datacore-one/datacore-dips`

Determine whether:

* an issue is actually a specification question;
* a PR implements an existing normative DIP;
* a PR contradicts a current DIP;
* a DIP has already been superseded;
* implementation landed but the DIP still claims otherwise;
* a proposed DIP is being incorrectly treated as current behavior.

Do not allow issue triage and implementation triage to drift from the specification model.

---

# 12. CI / REPOSITORY HEALTH

Look across relevant repos for systemic problems affecting development velocity.

Examples:

* frequently failing CI;
* flaky tests;
* branch protection blocking legitimate work;
* dependency-update failures;
* installation tests broken;
* release automation broken;
* required checks that no longer exist;
* stale bot PRs;
* conflicting lockfile updates.

Identify anything that repeatedly wastes developer time.

A shared CI problem that blocks five PRs may be the highest-priority task even if no explicit P0 issue exists.

---

# 13. SECURITY / DATA-SAFETY FLAGS

Do not conduct a full security audit unless necessary for reviewing a PR, but surface any issue/PR involving:

* security;
* credentials;
* authorization;
* isolation;
* data loss;
* migrations;
* destructive changes;
* synchronization;
* persistence;
* concurrency;
* external exposure.

These should receive enhanced scrutiny.

Never prioritize a feature release ahead of a known credible security/data-loss blocker without explicitly flagging the tradeoff.

---

# 14. MISSING ISSUES

Triage should also detect work that **should have a GitHub issue but does not**.

If investigation reveals a concrete unresolved problem that is:

* actionable;
* material;
* not already tracked;

identify it as:

> **MISSING ISSUE**

Provide:

**Suggested title:**
**Repository:**
**Problem:**
**Impact:**
**Acceptance criteria:**
**Priority:**

Do not generate speculative backlog clutter.

Only recommend issues that represent real work.

---

# 15. POORLY SPECIFIED ISSUES

Identify issues that cannot responsibly be implemented because they lack:

* objective;
* acceptance criteria;
* reproduction;
* relevant context;
* expected behavior;
* decision on important semantics.

Classify as:

> **NEEDS SPECIFICATION**

Explain exactly what is missing.

Do not recommend coding against unresolved product semantics.

---

# 16. PRIORITIZATION MODEL

Do not prioritize by issue number, age, or whoever complains loudest.

Prioritize using:

### P0 — STOP / IMMEDIATE

Examples:

* active security exposure;
* credible data loss;
* broken production;
* release would cause serious damage.

### P1 — UNBLOCK / RELEASE CRITICAL

Examples:

* blocks release;
* blocks multiple team members;
* shared CI/infrastructure blocker;
* required review preventing merge;
* critical dependency for several tasks.

### P2 — HIGH VALUE NEXT

Important work with meaningful product/reliability payoff but not currently blocking.

### P3 — BACKLOG

Useful but not urgent.

Within the same level, prioritize:

> **highest unblock leverage / lowest effort**

when reasonable.

A small action that unblocks three people should usually precede a large isolated improvement.

---

# 17. DISTINGUISH WORK TYPES

Every recommended next action should be classified as one of:

* **REVIEW**
* **MERGE**
* **FIX**
* **DECIDE**
* **SPECIFY**
* **REBASE**
* **TEST**
* **UNBLOCK**
* **CLOSE**
* **ASSIGN**
* **INVESTIGATE**

This makes the queue actionable.

---

# 18. DO NOT MODIFY GITHUB DURING TRIAGE

This pass is analytical.

Do not:

* merge PRs;
* close issues;
* create issues;
* change labels;
* change assignments;
* push commits;
* approve PRs;
* request changes on GitHub;

unless I explicitly ask you to execute those actions after reviewing the triage.

You may fully review PRs and recommend actions.

Preserve a clean separation between:

> **analysis**

and

> **GitHub mutation**.

---

# FINAL OUTPUT

Produce a concise decision-oriented report.

## 1. Executive summary

Answer:

> What deserves attention right now?

Include the 3–7 most consequential observations.

---

## 2. What I should do first

Give me an ordered queue:

| # | Action | Repo | Issue/PR | Why now | Unblocks | Priority |
| - | ------ | ---- | -------- | ------- | -------- | -------- |

Do not give me 30 equal-priority items.

Produce a real sequence.

---

## 3. Waiting on me

List:

### Assigned to me

### Review requested from me

### Decision/specification waiting on me

### My PRs needing action

Include exactly what I need to do next.

---

## 4. Team blockers

| Blocker | Repo | Blocks | Required action | Best owner | Priority |
| ------- | ---- | ------ | --------------- | ---------- | -------- |

---

## 5. Release status

State:

**READY**

or

**READY AFTER X**

or

**BLOCKED**

Then identify:

* release blockers;
* release risks;
* minimum path to release;
* items safe to defer.

If no identifiable upcoming release exists, say so instead of inventing one.

---

## 6. Open PR dashboard

For every relevant open PR:

| PR | Repo | Author | Purpose | CI | Review | Release impact | Verdict | Next action |
| -- | ---- | ------ | ------- | -- | ------ | -------------- | ------- | ----------- |

---

## 7. PR review findings

For each PR requiring substantive comments:

### PR #X — title

**Verdict:**
**Blockers:**
**Important findings:**
**Missing tests:**
**Recommended next action:**

Do not fill this section with cosmetic review comments.

---

## 8. Issue dashboard

Group relevant issues into:

### P0 — Immediate

### P1 — Unblock / release

### P2 — High-value next

### P3 — Backlog

For each show owner and next action.

---

## 9. Dependency / merge order

Show the critical execution sequence and safe parallel work.

Identify PRs/issues that should **not** be worked independently.

---

## 10. Stale / duplicate / close candidates

Identify unnecessary open work and explain why.

Do not recommend closure based purely on age.

---

## 11. Missing or underspecified work

Separate:

### Missing issues

### Needs specification

### Needs decision

Avoid speculative backlog generation.

---

## 12. Repository health

Highlight systemic developer-velocity problems such as:

* CI;
* flaky tests;
* dependency drift;
* release tooling;
* repeated merge conflicts;
* repository/spec drift.

---

## 13. Suggested work plan

Finish with:

### Today / next

Highest-leverage actions.

### After blockers clear

Work that becomes actionable next.

### Can run in parallel

Independent team work.

### Defer

Items that should not consume attention now.

---

# FINAL PRIORITIZATION PASS

Before returning the report ask:

> Is there a five-minute action that would unblock hours/days of someone else's work?

> Is a PR waiting only on review?

> Is an issue assigned to me that is no longer actually actionable?

> Is the team working around a shared blocker instead of fixing it?

> Is a release blocked by one small dependency?

> Are two developers solving the same problem?

> Is there a security/data-loss concern that should override feature priority?

> Is there a decision only I can make?

> Is there open work that is already solved?

> Which single action produces the most forward progress?

Use those answers to determine the final ordering.

---

# STANDARD

The standard is not:

> “Here are all open issues and PRs.”

The standard is:

> **“I understand the current state across the relevant GitHub repositories, know what is waiting on whom, have substantively reviewed the open PRs, identified release and team blockers, removed noise from the picture, and can tell you exactly what should happen next to maximize forward progress.”**
