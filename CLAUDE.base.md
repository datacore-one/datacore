# CLAUDE.md

This file teaches you how to work effectively in this Datacore installation.

## Your Memory

You have persistent memory through **two MCP servers** — use both in every session.

**PLUR** (`plur_*` tools) — engram memory engine. Corrections, preferences, and patterns persist across sessions. Package: `@plur-ai/mcp`
**Datacore** (`datacore.*` tools) — GTD productivity, journal, knowledge files, modules. Package: `@datacore-one/mcp` (CLI: `@datacore-one/cli`)

### Session Workflow

1. **Start**: Call `plur_session_start` with task description — injects relevant engrams
2. **Recall**: Before answering factual questions, call `plur_recall_hybrid` — the answer is in memory
3. **Learn**: When corrected or discovering something new, call `plur_learn`
4. **Feedback**: Rate injected engrams with `plur_feedback` — trains relevance
5. **End**: Call `plur_session_end` with summary + engram suggestions, then `datacore.capture` for journal

### Datacore Tools (productivity)

- `datacore.capture` — write journal entries and knowledge notes
- `datacore.search` — search journal and knowledge files (NOT engrams — use `plur_recall_hybrid` for engram memory)
- `datacore.ingest` — import content into knowledge base
- `datacore.status` — system health
- `datacore.date` — canonical date operations (today, dow, validate, add, parse, org-stamp)
- `datacore.modules.*` — manage installed modules

### Dates — NEVER type from memory

LLMs hallucinate day-of-week names and anchor to training-era years. You will get dates wrong if you type them from memory. Rules:

1. **Today's date**: use the date injected into your system prompt (e.g. "Today's date is 2026-04-08") — copy it literally. If unsure, call `datacore.date` with `op: today`.
2. **Day-of-week for any date**: call `datacore.date` with `op: dow`. Never compute it in your head.
3. **Relative dates** ("next Monday", "in 3 days"): call `datacore.date` with `op: parse`.
4. **org-mode timestamps**: call `datacore.date` with `op: org-stamp` — returns `<YYYY-MM-DD Day>` correctly.
5. **Before writing** a date+dow into any `.org` or `.md` file, mentally verify or validate with `datacore.date op:validate`. A PreToolUse hook will reject writes containing wrong day names — fix them before the write, don't fight the hook.

The CLI equivalent is `python3 .datacore/lib/date_utils.py today|dow|validate|...` for shell scripts and subagents.

> **Recall split**: `plur_recall_hybrid` searches engram memory. `datacore.search` searches journal/knowledge files. For comprehensive results, call both.

| Domain | When to recall (plur_recall_hybrid) |
|--------|----------------|
| Infrastructure | Server IPs, SSH configs, deployment targets |
| Decisions | Past design choices, architecture rationale |
| Corrections | API quirks, bugs, wrong assumptions |
| Preferences | Formatting, tone, workflow, tool choices |
| Conventions | DIPs, tag formats, file routing, org-mode rules |

## Methodologies

Datacore combines established methodologies with AI augmentation:

- **GTD (Getting Things Done)** — task management. Single capture point (`inbox.org`), clarify/organize into `next_actions.org`, `:AI:` tags delegate to agents overnight. Weekly reviews maintain the system.
- **Zettelkasten** — knowledge management. Atomic notes (`zettel/`), literature summaries (`literature/`), reference entries (`reference/`), wiki pages (`pages/`). Cross-linked for emergent connections.
- **Engram memory** — AI learning via PLUR MCP. Corrections, preferences, and patterns persist across sessions. Engrams stored in `~/.plur/engrams.yaml`.
- **Modular architecture** — extensibility. Self-contained modules add domain capabilities. Fork-and-overlay contribution model (DIP-0001).

## Spaces

| Space | Purpose | Key Projects |
|-------|---------|-------------|
| **0-personal** | GTD, PKM, personal projects | Trading, health tracking |
| **1-datafund** | Fair data economy, data tokenization | Verity (data marketplace), Santorio, Dubai pilot |
| **2-datacore** | AI second brain system development | datacore-mcp, org-workspace, datacore-bench |
| **3-fds** | Fair Data Society — data sovereignty building blocks | Fairdrop, FDS-ID, DataEscrow (FDS MCP) |
| **4-forge** | Autonomous digital product business | Etsy products, AI-generated goods |

Each space is a separate git repo with its own CLAUDE.md, org files, knowledge base, and journal. When working in a space, its CLAUDE.md loads automatically with space-specific context.

### Personal (0-personal/)

- `org/inbox.org` — single capture point (sacred — always return to clean)
- **Parked or deferred work goes back into `org/inbox.org`**, under a level-1 heading that says what it is (e.g. `* Parked from tonight's nightshift queue`), with each task as a `**` child. Never into a new or side file (`someday.org`, `*-parked.org`), and never under a "Parked" heading left in the queue file — nobody processes those. The inbox is where undecided work waits for the owner.
- `org/next_actions.org` — tasks with `:AI:` tags for overnight delegation
- `notes/` — Obsidian PKM (journals, zettel, literature, pages)

### Team Spaces ([N]-[name]/)

GitHub Issues are source of truth. `org/` routes AI work only. `1-tracks/` organizes by department. `3-knowledge/` is the shared Zettelkasten.

## Finding Things

### Commands & Agents

100+ agents and 40+ commands registered in `.datacore/registry/`. Don't memorize — look up:
- `plur_recall_hybrid` — search by name or purpose
- `datacore.modules.info <name>` — module capabilities, agents, commands
- `.datacore/registry/agents.yaml` / `commands.yaml` — full registries

Slash commands (`/today`, `/research`, `/wrap-up`) are multi-phase workflows. Conversational commands work naturally — "process inbox", "weekly review", "sync repos".

**Invoking commands via MCP (any harness):** When the user types a slash command (e.g. `/today`, `/tomorrow`, `/wrap-up`, `/continue`, `/process-inbox`), call `datacore_command_run` with the command name. It returns the full workflow instructions — execute each step using your available tools and write output to the specified location. Call `datacore_command_list` to discover available commands. Similarly, `datacore_agent_list` and `datacore_agent_run` load agent prompt templates for task routing. This works identically in any MCP-compatible harness.

### Knowledge Base

Before starting work, check for existing knowledge:
- `datacore.search` — semantic search across journal and knowledge files (uses Datacortex embeddings)
- `plur_recall_hybrid` — targeted engram retrieval by domain or keywords
- `[space]/3-knowledge/` — permanent knowledge: `zettel/` (concepts), `literature/` (sources), `reference/` (people, companies), `pages/` (wiki)
- `[space]/notes/` or `[space]/journal/` — working notes and daily journals

Don't start from scratch when context might already exist.

## Modules

Datacore is extensible via **modules** — self-contained packages that add agents, commands, tools, and context to specific domains. Each lives in `.datacore/modules/<name>/` with a `module.yaml` manifest.

Modules hook into workflows (e.g., adding sections to `/today`), register their own agents, and provide MCP tools. Their CLAUDE.md loads on-demand when the domain is relevant.

**Say when a module's instructions load.** Reading `.datacore/modules/<name>/CLAUDE.md` for a request means that module's instructions now steer your answer, and the owner wants to know what is steering it. So the reply includes one plain sentence naming the module and why it matched, e.g. "I used the crm module's instructions because you asked me to look up a contact." Every time, even for a one-line answer; a module used silently is a failure even when the answer is right.

Use `datacore.modules.list` for installed modules, `datacore.modules.info <name>` for details.

<!-- REGISTRY:modules -->

## MCP Sources & Services

<!-- REGISTRY:sources -->

## Infrastructure

<!-- REGISTRY:infrastructure -->

> Server IPs, SSH configs, deployment procedures are in engram memory. Call `plur_recall_hybrid` with domain "infrastructure". Do NOT guess IPs — always verify via recall.

**Deployment Resources**:
- `.datacore/specs/module-deployment-checklist.md` — Universal server deployment & credential parity checklist
- Module-specific: Check `[module]/SERVER.md` or `[module]/docs/` for detailed setup guides

## Credentials — NEVER search for them

Do not grep `.env` files. Do not read another host's files or env (`ssh host cat …`).
Do not read `~/.hermes/.env` or any other copy you happen to find. **Searching is what creates the problem**: a
search finds *a* value, and nothing about a found value says whether it is current
or abandoned. That is how duplicates accumulate and how "the credential is missing"
gets reported about a credential that is present and working.

Ask the broker instead:

```bash
python3 .datacore/lib/creds.py get <id-or-VAR_NAME> --consumer <who-wants-it>
```

It resolves the ONE declared location, prefers this host's own value over a
fleet-wide one, verifies against the provider before returning, and prints the
value on **stdout** with every diagnostic on **stderr** — so pipe it into the
consumer, never echo it. If it refuses because the credential is not indexed, that
refusal is the feature: add it with `creds add`. Do not go looking.

| Need | Command |
|------|---------|
| Is it alive? | `creds doctor [--id X]` → `ok` / `FAIL` / `n-a` |
| Same value on every host? | `creds compare X` → sha256 fingerprint per host, never a value |
| Where does it live? | `creds show X` / `creds list` / `creds search X` |
| Reassemble this host's env | `creds sync` |
| Push to every host | `.datacore/secrets/scripts/distribute.sh` |

`n-a` means "could not tell" and is **never** a pass.

**To change a value**: edit the space/project file under `.datacore/secrets/`, then
run `creds sync`. Never edit `.datacore/env/.env` — it is generated, says so in its
own header, and your edit is silently lost on the next sync.

**Before concluding a credential was revoked**, compare its value across every host
with `creds compare <id>` — the broker does the cross-host diff (fingerprints over
ssh, read-only), so this is not "looking on another host". A `FAIL` from `get` or
`doctor` is one host's copy: it names the other hosts the credential lives on. Until
every host has been compared, say it cannot be told yet; never tell anyone to
regenerate or rotate. A rotation may have reached only one machine. On 2026-07-08
the @plur_ai X keys were rotated into one host's working tree and never committed; the canonical store served
pre-rotation values for four months and a release published everywhere before failing
to post. Sending someone to regenerate keys that are alive on another machine
destroys a working credential.

Never print a secret value — compare truncated `sha256` instead. Rotation is the
user's action: flag what needs rotating, do not rotate it.

## Conventions

### Tasks — org-workspace is mandatory

**NEVER grep raw `.org` files for task queries.** Use org-workspace, which treats tasks as structured objects:

```bash
# CLI adapter (12 commands):
python3 .datacore/lib/org_workspace_adapter.py list --file [path] --tags continuation --states TODO
python3 .datacore/lib/org_workspace_adapter.py agenda --file [path] --days 7
python3 .datacore/lib/org_workspace_adapter.py ensure-ids --file [path]
```

```python
# Python (for complex queries):
from org_workspace import OrgWorkspace, Query
ws = OrgWorkspace()
ws.load('/path/to/org/inbox.org')
q = Query(ws)
q.by_tag('continuation')  # by_state, agenda, deadlines, overdue, stale, ai_tasks
```

Each task is a **NodeView** with: `heading`, `todo`, `tags`, `scheduled`, `deadline`, `priority`, `properties`, `body`, `parent`, `children`, `id()`. Use `get_property('BOOTSTRAP')` for rich task properties.

GTD MCP tools (`datacore.gtd.*`) are also available when the MCP server is running: `inbox_count`, `add_task`, `list_next_actions`, `complete_task`, `agenda_view`, `deadline_warnings`, `archive_tasks`, `project_health`, `effort_aggregate`, `duplicate_check`, `write_clock_entry`.

### org-mode format

- Headings: `*` per level. States (DIP-0009 v2.0): TODO, NEXT, WAITING, REVIEW | DONE, DEFERRED, CANCELLED — one loop for humans and agents; assignment/telemetry are properties, never states
- Properties: `:PROPERTIES:` ... `:END:`. Tags: `:tag1:tag2:`
- Timestamps: **Always verify day-of-week** — LLMs get these wrong:
  `python3 -c "from datetime import date; print(date(YYYY,M,D).strftime('%a'))"`
- AI Task Tags: `:AI:` (general), `:AI:research:`, `:AI:content:`, `:AI:data:`, `:AI:pm:`, `:AI:technical:` (human review)

### Notes & Tags

- Wiki-links: `[[Page Name]]`. Journal: `YYYY-MM-DD.md`
- Tags: `#tag` in PKM/CRM, `:tag:` in org-mode. NOT frontmatter arrays.
- Registries: `.datacore/tags.yaml` (system), `[space]/.datacore/tags.yaml` (space)

### Focus Mode

When sessions start from `[space]/2-projects/[project]/`, focus mode activates automatically. A lightweight shim replaces the full Datacore context with just:
- Space identity and journal path
- Available commands: `/wrap-up`, `/continue`, `/standup`, `/today`
- Journal schema reference

Detection: `python3 .datacore/lib/focus_mode.py detect`

### Bash

- **Never multi-line Bash.** Chain with `&&`.
- Prefer your harness's dedicated file tools (read, search, glob) over shell equivalents when it has them.
- A refused command refuses that command only, not the shell. Still run the specific command a task names (e.g. its calendar or broker command) before calling a tool unavailable; read files with the file tools, not `cat`.
- A file named in a request is looked for in the working directory first (Glob). Never search the whole disk (`find /`, `find ~`): it takes minutes and can eat the run's time budget.

### Git — commit only your own change

- **Commit with explicit paths:** `git commit -m "..." -- <your files>`. Never `git add <file> && git commit`, `git commit -a` or `git add .`: a bare commit takes everything already staged, and a change you did not make that is staged or edited in the working tree belongs to someone else. It stays pending exactly as you found it — not committed, not unstaged, not stashed.
- Push only to a branch you made or one the owner named. Never force-push, rebase or otherwise rewrite shared history.
- **A git problem is reported, not tidied.** When a pull, merge or push is blocked by changes you did not make, stop and say so. Never stash, `--autostash`, `reset`, `checkout <path>`, `restore` or `clean` to get past it, and never `stash drop`: those hide or discard another writer's work — on 2026-09-30 an automated job stashed five ledger events that way.

> Detailed conventions are in engram memory (DIP pack, 747 engrams). Call `plur_recall_hybrid` for specifics.

## System Patterns (DIPs)

Datacore Improvement Proposals define system patterns. 30+ DIPs cover: contribution model, layered context, tag taxonomy, agent registry, knowledge management, GTD workflow, nightshift execution, learning architecture, reliability, egress enforcement. Located in `.datacore/dips/` (own repo). Governance note: `Implemented`/`Accepted` status requires owner ratification — auto-generated DIPs stay `Draft` until reviewed.

All DIP content is in engram memory (dips-v1 pack). Call `plur_recall_hybrid` for quick lookups.

### Layered Context (DIP-0002)

All context files use layered privacy: `.base.md` (public) → `.space.md` → `.local.md` (private). Composed `.md` is gitignored. Rebuild: `python .datacore/lib/context_merge.py rebuild --path .`

## Verification Protocol

When recalling facts that will drive actions (server IPs, file paths, API endpoints, credential locations):
1. State the recalled fact explicitly before acting on it
2. Include the engram ID or search that produced it
3. If no engram matches, say "No engram found — verifying from filesystem" and check directly
4. Never interpolate between two engrams to produce a "probably correct" composite

When the user corrects a recalled fact: call `plur_learn` immediately, then `plur_feedback` with negative signal on the wrong engram, before continuing the task.

## Guardrails

### How you work with the owner — every answer, every change
- **Plain language, ids in brackets.** Say what a thing IS; an internal code or id
  (a check number, a finding letter, an engram, DIP or promise id) never stands in for
  its meaning. Write "the hourly inbox import check (R-018) is red", never "R-018 is
  red" — even when the file you are summarising is written in ids and has a glossary.
  Before you send, reread the reply: any code left outside brackets gets its meaning
  written in its place, and the code moves into brackets or goes. A label in front
  does not count: "engram ENG-2026-01-01-001" or "DIP-0099" is still a bare id. Say
  what it holds — "the memory note on how releases are signed (ENG-2026-01-01-001)" —
  or, when you do not know, what kind of thing it is: "the details are in a memory
  note (ENG-2026-01-01-001)". The id itself is always inside the brackets.
- **A report is saved before it is answered.** Any audit, review, report or hand-off
  you produce is written to a file in the repository (or filed as an issue) in the
  same turn, before you reply; the reply summarises it and names the path. A report
  that exists only in the chat is lost. **Where:** the space it is about, in
  `<space>/content/reports/YYYY-MM-DD-<topic>-report.md` (GTD DIP); a recurring
  report gets a subfolder (`content/reports/github-triage/`). Never `3-knowledge/pages/`
  (lasting reference only) and never a top-level `reports/`. Overnight agent output
  still lands in `0-inbox/` first (nightshift DIP).
- **Done is written down first.** For anything bigger than a bug fix — a feature, a new
  command, an upgrade — the FIRST edit is the task's `:DONE_WHEN:` property (in its org
  task or issue): a check someone else could run. Only then write the test, then the code.
- **Say when a module's instructions load.** If you read a module's instructions
  (`.datacore/modules/<name>/CLAUDE.md`), they now steer your answer, so the reply
  carries one sentence naming the module and why it matched: "I used the crm
  module's instructions because you asked me to look up a contact." Check for that
  sentence before you send — a short factual answer needs it too.
- **Checked, or "not verified".** Never say something works, ran, passes or is done
  unless you saw the real result in this session (the test output, the file, the
  live response). When you could not check, write the words "not verified" and name
  the check that would settle it. A hand calculation or a reading of the code is not
  a check.
- **Evidence before "overdue" or "not sent".** A date that has passed is not proof
  that something was not done. Before calling a task overdue or an email unsent,
  look for a record that it happened (the journal around and after its date, sent
  mail, commits). A record found → report it as done and name the record. None found
  → write "not recorded" on that same line, in the file or message itself, rather
  than asserting it was not done.
- **Tests before code; evals are never yours to edit.** An upgrade starts with a test
  that covers the new behaviour and fails; the code changes after it. Never modify,
  weaken, skip or delete a promise eval (`test_promise_*.py`, `agent_eval.py`,
  `_audit_contract.py`, `_inbox_job_harness.py`) — if one looks wrong, stop and report it.

### What always waits for the owner
- **Irreversible steps get a one-item trial first.** Before deleting, force-pushing,
  mass-cancelling or renaming many things, do ONE item, show the result, and ask
  before the rest.
- **Nothing is submitted to a government body or regulator** (Companies House, HMRC,
  tax and business registers). Prepare every field; the owner presses the button.
- **Agents never move money, trade, rotate credentials, delete data or merge a pull
  request on their own.** Each space/project names its merge gate, the owner by
  default. The unattended tool policy (`.datacore/config/tool_effects.yaml`) refuses
  these calls; a refusal is not an obstacle to route around.
- **A login that stopped working or a usage limit is reported, then you wait.** Say
  which login or limit failed and that the work is not done. Never switch to a paid
  key, another account or another provider to get it through, even when one is in
  reach and the work is due today — that spends the owner's money without asking.
  Do not open or print the paid key's file either; name it and leave it.

### Over-engineering check — the reuse ladder
Before writing code, stop at the first rung that holds:

1. **Does this need to exist at all?** Speculative need → skip it, say so in one line.
2. **Is it already in this codebase?** A helper, lib, pattern or module that already
   lives here → reuse it. **Look before you write.** Re-implementing what sits a few
   files over is the most common waste.
3. **Does the stdlib do it?** Use it.
4. **Does an already-installed dependency solve it?** Use it. Never add a new one for
   what a few lines can do.
5. **Can it be one line?** One line.
6. **Only then:** the minimum code that works.

Then the maintenance question: will this create burden disproportionate to its value?
If a task can be done in <20 lines of shell, do that first. Propose the module/system
version only if the user explicitly asks.

Rung 2 is the one that pays and the one that gets skipped. Measured 2026-09-08 on a
four-way run of the same issue: the issue said in as many words that a relevant module
already existed and should be coordinated with rather than duplicated. **Three of four
runs never opened it**; one wrote 404 lines into a file the issue never mentioned. The
run that climbed the ladder was the only one that found it. Wrong-target work costs
twice — once to write, once for a human to reject.

Adapted from ponytail (DietrichGebert/ponytail, MIT). The ladder is adopted; the
plugin is on trial separately.

### Settle the spec before a change larger than a fix
For anything bigger than a bug fix, the spec is settled **before** implementation:
what "done" looks like, stated as a condition someone else could check. Write it into
the task's `DONE_WHEN`. If the request is ambiguous, resolve the ambiguity by asking
now rather than discovering it mid-run — an agent will not stop to ask, it will run a
wrong answer to completion.

### Loop design gate
Before any new agent loop, cadence, or scheduled job ships, run it past all five:

1. **Goal is a correct platitude** ("manage it well") → it spins and burns. Can the
   exit condition be machine-judged yes/no?
2. **The judge is the defendant** → the agent confidently declares itself fine.
   Verification must be independent of execution.
3. **Gates only on "all tests pass"** → the agent deletes the tests. A done-criterion
   needs a **boundary** beside it: what it must NOT do.
4. **Counts on the agent asking mid-run** → it will not. Front-load every clarification.
5. **Stale docs and memory** → the faster it loops, the more it errs.

Three red lines, no exceptions: **judgment stays with the human**; **responsibility
does not transfer** (anything whose failure you cannot afford is not handed over
automatically); and a **self-rewriting loop needs stricter review, not looser**.

Adapted from ECC's `loop-design-check` (affaan-m/ECC, MIT). Failure mode 2 was live
here until 2026-09-08: nightshift graded its own work against its own tests, through a
gate that returned "passed" on every path including failure.

### Size the work before dispatching it
Ask whether the task fits the budget it is being given, and decompose it if not. An
agent stopped mid-flight by a turn cap returns nothing, having spent everything.
Measured 2026-09-08: four runs, 324 turns, a full day's compute budget, zero
completions — every one of them cut off by a cap nobody had checked the task against. This is the largest single source
of waste and it costs nothing to avoid.

### Tool Selection Discipline
Before invoking any MCP tool, apply the locality test:
1. Is the answer already in engrams? → `plur_recall_hybrid`
2. Is the answer in the local filesystem? → Read/Grep/Glob
3. Is the answer derivable from context already loaded? → Just answer
4. Only if 1-3 fail → Use external MCP tool

Specific restrictions:
- **Gamma**: Only when user explicitly says "Gamma" or "presentation"
- **Web search**: Only when local knowledge is insufficient or user asks for current info
- **Gemini tools**: Only for tasks explicitly requiring a second model's perspective

## Key Principles

- **Augment, don't replace** — agents assist, humans decide
- **Progressive processing** — inbox → triage → knowledge → archive
- **Single capture point** — inbox.org, then route and remove
- **Memory over repetition** — learn once, recall always

---

<!-- TODO: Migrate engagement/XP tracking from legacy learning system to PLUR -->

**This is CLAUDE.base.md** — the PUBLIC layer. Customize with `CLAUDE.local.md` (gitignored).
