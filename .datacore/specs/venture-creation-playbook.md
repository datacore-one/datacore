# Creating a venture in Datacore — the whole path

Written 2026-09-18 by reading the code and the seven ventures that already run,
not from the design docs. Where a file and its documentation disagreed, the file
won and the disagreement is noted.

A **venture** in Datacore is a numbered space at `~/Data/[N]-<slug>/` that owns
a `venture.yaml`. That file is what makes the heartbeat wake it, the `/today`
briefing report it, and the MCP tools list it. Everything else in this document
is a layer on top of that one file.

Read this as a sequence, but read **The minimum that is actually a venture**
(near the end) first. Most of what follows should not exist on day one.

---

## The nine stages

| # | Stage | Produces | Gate to the next |
|---|-------|----------|------------------|
| 0 | Decide it is a venture | design doc | it needs its own clock |
| 1 | Space + constitution | `venture.yaml` | `load_venture` passes |
| 2 | Intent graph | `org/intents.org` | goals carry `:SUCCESS:` |
| 3 | Hypothesis board | `hypotheses.yaml` | one hypothesis is `active` |
| 4 | Brand and positioning | `1-tracks/comms/` | voice is written down |
| 5 | Roadmap | `roadmap.yaml` | `roadmap_validate` passes |
| 6 | Roles and cadences | role files, `cadences:` | one cadence completes |
| 7 | Sprints and execution | `sprints/<id>/sprint.yaml` | `sprint_sync --apply` runs |
| 8 | Visibility | `/today` section, ledger | the owner sees it without asking |
| 9 | Stage transitions | `stage:` moves | evidence, not enthusiasm |

Stages 2 and 5 are ordered that way on purpose: a roadmap item must name an
intent it serves, and `roadmap_validate.py` treats an unresolvable `serves:` as
an error, not a warning. Build the graph first or the roadmap cannot be checked.

---

## Stage 0 — Decide it is a venture

Not every idea earns a space. A venture is warranted when the work has **its own
clock** — its own cadences, its own budget, its own definition of done that no
existing space is accountable for. Otherwise it is a track or a project inside a
space that already exists.

Run the diagnostic before scaffolding anything:

```
/office-hours          # ventures module — six forcing questions, Startup or Builder mode
```

It produces `design-docs/<topic>-design-<YYYYMMDD>.md` and, deliberately, **no
code and no PR**. If the six questions do not survive contact, stop here; the
cheapest venture is the one not created.

If the idea is strategically large, follow with `/ceo-review` (four scope modes:
EXPANSION, SELECTIVE EXPANSION, HOLD, REDUCTION) before committing. Capture the
reason you decided yes — the create tool takes it as `triggering_decision` and
files it as the venture's first decision record. A venture whose founding
rationale is not written down cannot be honestly archived later.

---

## Stage 1 — The space and the constitution

### Path A — the app (preferred)

```
ventures_create_venture(name, stage, template, mission, triggering_decision)
```

The daemon (`datacore-app/daemon/datacored/adapters/spaces.py`) does the work:
slugifies the name, takes the **next free numeric prefix** (never reuses a gap,
so history stays stable), builds the whole tree in a temp dir, validates that
`venture.yaml` parses and `load_venture` passes, and only then does an atomic
rename into place. On collision it refuses.

Templates seed roles and tracks, nothing more:

| Template | Roles | Tracks |
|----------|-------|--------|
| `blank` | — | — |
| `hedge_fund` | cio, trader | research, ops |
| `saas` | founder, engineer | product, dev |
| `content` | operator | comms |

What lands: `venture.yaml`, `_index.md`, `.gitignore`, the six standard subdirs
(`0-inbox`, `1-tracks`, `2-projects`, `3-knowledge`, `4-archive`, `journal`),
four org files (`inbox.org`, `next_actions.org`, `projects.org`, `someday.org`,
each with the canonical `#+SEQ_TODO` line per DIP-0009 v2), `.datacore/config.yaml`,
and the layered `CLAUDE.base.md` / `CLAUDE.space.md` pair per DIP-0002.

Note what does **not** land: no `roadmap.yaml`, no `intents.org`, no
`hypotheses.yaml`, no roles directory contents. Stages 2–7 are yours.

### Path B — by hand

Copy the shape of a venture that already validates. Do **not** copy
`.datacore/templates/space/venture.yaml.template` verbatim: it writes `roles:`
as a **list** of `{id, agent, cadence}` entries, and `VentureConfig` requires a
**dict** of role name → `RoleConfig`. The template is stale; the loader is the
contract.

### The schema that actually applies

`.datacore/modules/ventures/lib/venture_loader.py`, Pydantic v2:

```yaml
name: my-venture              # required, non-empty
description: "One sentence."
stage: discovery              # proposed|discovery|validation|growth|maturity|archived
space: "9-my-venture"
autonomy: 1                   # 0=off 1=suggest 2=draft 3=act
thesis: |                     # what you believe, what would disprove it
north_star: signed_clients    # the one number (see the warning below)
target_customer: "..."
budget:
  ceiling: 100.0              # ai_tokens + real_spend MUST be <= ceiling
  ai_tokens: 80.0
  real_spend: 20.0
  approval_threshold: 25.0
  ledger: .datacore/state/venture/budget-ledger.yaml
heartbeat:
  triggers: [cadences]        # subset of cadences|hypotheses|inbox|github; [] disables
roles:                        # dict, not list
  ceo:
    description: "..."
    cadences: {weekly: [strategy-review]}
    budget_authority: 30
hypotheses_file: hypotheses.yaml
modules:
  required: [ventures]
  optional: [comms, github, crm]
tracks: [ops, product]        # strings, or {name: [subtracks]}
github: {org: my-org, repos: [{name: core, role: core}]}
nightshift: {enabled: true, max_parallel_agents: 2, timeout_minutes: 60}
tags: [...]
```

Enforcement that will bite you:

- **Sub-blocks forbid extras.** `budget`, `github.repos[]`, `nightshift` reject
  unknown keys — a typo is an error, not a silent drop. Top-level and `roles.*`
  allow extras so real files can carry documentation fields.
  (A `description:` on a repo entry was an error until 2026-09-16 and took the
  whole heartbeat down. The omission was in the schema, not the file.)
- **Budget invariant.** `ai_tokens + real_spend <= ceiling`, checked at load.
- **Cadence frequencies** must be one of `daily`, `weekly`, `monthly`,
  `quarterly`. Nothing else exists. Names must be unique within a frequency.
- **Stage transitions** are a state machine, not a free field:

  ```
  proposed   → discovery | archived
  discovery  → validation | archived
  validation → growth | discovery | archived
  growth     → maturity | validation | archived
  maturity   → growth | archived
  archived   → discovery              (restore only)
  ```

**On `north_star`:** one metric per venture is a trap when the venture has more
than one motion. PLUR carried `1000 enterprise installs by 2026-10-26` across a
sales cycle *and* an ecosystem — two different physics, one number that could
not be true of both. It was replaced on 2026-09-01 by three goals with three
clocks in `roadmap.yaml`, and `venture.yaml` still records the superseded metric.
If your venture has two motions, expect to carry the goals in the roadmap and
keep `north_star` only for the tooling that reads it.

Validate before moving on:

```bash
python3 -c "import sys; sys.path.insert(0,'.datacore/modules/ventures/lib'); \
from venture_loader import load_venture; print(load_venture('9-my-venture/venture.yaml').name)"
```

---

## Stage 2 — The intent graph

`org/intents.org` answers *why does this matter*. Seed it from what the space
already declares rather than authoring from a blank page:

```bash
python3 .datacore/lib/intent_graph_scaffold.py --space 9-my-venture --dry-run
python3 .datacore/lib/intent_graph_scaffold.py --space 9-my-venture
```

The scaffold turns `thesis` into the vision node, `north_star` into a level-1
intent, and each role plus its cadences into branches. Every derived node is
tagged `DRAFT` and **carries no `:SUCCESS:`** — deliberately. An invented metric
reads as real and is worse than a visible blank.

Your job is the part it refused to do: write the success criteria, then drop the
`DRAFT` tags. Structure:

```
Vision → Intents → Strategic goals (with :SUCCESS:) → Initiatives → Projects
```

Heading depth is the parent edge. A `:SERVES:` property adds cross-branch
parents and **may point into another space** (`5-plur:knowledge-exchange`) —
that is how ventures that serve each other stay visible as one graph under the
personal view. Multi-parent nodes are high-leverage by design and get worked
first. IDs are namespaced by space, so two ventures can both have a `growth`
node without colliding.

Then check it against reality rather than against itself:

```bash
python3 .datacore/lib/intent_outline.py --space 9-my-venture   # readable form for markup
python3 .datacore/lib/intent_tasks.py                          # task coverage
python3 .datacore/lib/intent_merge.py                          # top-down vs bottom-up
```

`intent_tasks.py` places real tasks on the graph by declaration first
(`:INTENT:` property), then focus-area tag from `tags.yaml`, then keyword
overlap as a last resort. **Tasks that fit nowhere are the finding, not an
error to suppress**: either the work serves nothing stated, or the graph is
missing a branch.

`intent_merge.py` is the one that catches what neither view catches alone. It
classifies every node as `confirmed`, `orphan` (work with no intent above it),
`dormant` (intent with no work beneath it), or **`reversed`** — intent still
stated, but the record says it was killed. The token strategy sat in the
Datacore graph with 19 tasks under it for two months after the fundraising plan
recorded "Token killed". Note its rule: **configuration is not evidence.**
`venture.yaml` cadences say what someone declared should recur; they say nothing
about anything having happened.

---

## Stage 3 — The hypothesis board

At `discovery` and `validation` stage this is the real work; the roadmap is
premature until something here is validated.

`hypotheses.yaml` at the space root (path configurable via `hypotheses_file`):

```yaml
venture: my-venture
board:
  backlog: []
  active:
    - id: mv-H001
      statement: "Caregiver niche converts at 3%+ on Etsy organic"
      metric: "3% conversion over 200 visitors within 30 days of publish"
      budget: 30
      status: active
      experiments: 0
      current_result: "..."
      last_reviewed: "2026-09-18"
  validated: []
  invalidated: []
```

The four canonical lanes are `backlog`, `active`, `validated`, `invalidated`.
Statuses map onto them: `proposed`/`backlog` → backlog; `active`/`in_progress`/
`running`/`testing` → active; `validated`/`verified` → validated;
`invalidated`/`falsified` → invalidated. **A status outside that mapping becomes
its own lane** when you update it — useful for `cold` or `infeasible`, but know
that you are creating a lane, not annotating a row.

A hypothesis needs a `metric` with a threshold, a window, and a budget, or it
cannot be settled. Forge's H001 sat `ASSETS_READY` for months and its
measurement window closed without the experiment ever running — the record now
says, correctly, "hypothesis not disproven, only untested." Write the requeue
date when you park one.

Wire a `hypothesis-review` cadence (template exists) so cold hypotheses surface
instead of ageing quietly.

---

## Stage 4 — Brand and positioning

Owned by the **comms** module. Add it to `modules.required` before you start.

The positioning agent uses StoryBrand (Donald Miller) and Tribal Marketing (Seth
Godin), and its core rule is that **the customer is the hero and the brand is
the guide**:

```
:AI:comms:brand:        # tag a task to trigger brand-positioning-agent
```

Note a gap: `brand-positioning` is declared under `provides.commands` in
`.datacore/modules/comms/module.yaml`, but `commands/brand-positioning.md` does
not exist. The agent works; the slash command does not. Use the tag, or add the
command file.

It wants a brief — name, tagline, what it does, the mechanism, differentiators,
primary audience — and produces BrandScript, Tribe Profile (smallest viable
audience plus sneezers), voice guidelines, and a Purple Cow angle.

Where the outputs live in a space (per `SCAFFOLDING.base.md`, DIP-0003):

| Artefact | Path |
|----------|------|
| Positioning docs | `1-tracks/comms/positioning/` |
| Brand guidelines | `1-tracks/comms/brand/guidelines.md` |
| Voice and tone | `1-tracks/comms/brand/voice.md` (or `voice.yaml`) |
| Key messages | `1-tracks/comms/messaging.md` |
| Purpose / vision / mission / values | `3-knowledge/pages/_core/` |
| Positioning / north star | `3-knowledge/pages/_core/` |
| Glossary | `3-knowledge/reference/glossary.md` |

Two worked examples in the tree: `3-fds/1-tracks/comms/positioning/` is the
minimum honest version (brandscript, tribe profile, `voice.yaml`).
`5-plur/1-tracks/comms/brand/` is the full build — `tokens.json` + `tokens.css`
as the source, with `build_guide.py`, `build_assets.py`, `build_merch.py`,
`build_motion_css.py` generating the guide, the assets and the PDF. Start at the
first; the second is what a growth-stage venture grows into.

Run `/scaffolding-audit` to see which identity documents are still missing.
Status values are `missing | stub | draft | reviewed | published` — a stub
generated by an agent is not a position.

---

## Stage 5 — The roadmap

Only once something is validated. Use the skill — it encodes the PLUR rebuild
(five drifted documents to one validated source of 93 epics and 750 tasks):

```
/roadmap-build
```

Its one claim: **a roadmap is good when it is CHECKABLE, not when it is well
written.** Every structural finding in that rebuild came from a script. The
prose was fine throughout. The prose is always fine.

`<space>/roadmap.yaml` is the single source; every human-readable roadmap is
**generated** from it. The rules the validator enforces:

- Outcome-level only, 30–50 items. Not a backlog — tasks and issues stay where
  they are and link upward.
- Every item **must** `serves:` at least one `INTENT_ID` from `org/intents.org`.
  An unresolvable reference is an error. An item serving nothing is a deletion
  candidate.
- `gate:` states a condition, never a date. `horizon: gated` is not scheduled
  and must not be picked up by sprint planning.
- `blocked_on: standing_block` is never selected by sprint planning.
- `shipped: false` is required on anything a public claim could rest on; a
  customer-facing view must refuse to emit it.
- `outcome:` is what changes in the world, not what gets built.
- Unknown keys are an error. Silent acceptance is how parallel tracking systems
  grow.

An item, in full:

```yaml
- id: R-001
  milestone: M2                  # or `continuous` — work that runs alongside the ladder
  track: enterprise
  lane: prod
  title: First customer deployment reaches steady state
  done_when:
    condition: >-
      Runs unattended for 14 consecutive days with no human intervention
    evidence: metric              # metric | test | artifact
    verify: >-
      gh issue list --repo org/repo --label incident --search "created:>14d ago" returns none
  outcome: The first signed customer is running unattended and is referenceable
  serves: [enterprise-beachhead]  # INTENT_IDs
  drive: status
  horizon: now                    # now | next | later | gated
  status: in_progress
  blocked_on: null                # null | human | standing_block | <id>
  delegable: false
  owner: gregor
  shipped: false
```

Three standing checks, all re-runnable:

```bash
python3 .datacore/lib/roadmap_validate.py --space 9-my-venture
python3 .datacore/lib/roadmap_validate.py --space 9-my-venture --coverage
python3 .datacore/lib/roadmap_render.py   --space 9-my-venture --html out.html
python3 .datacore/lib/roadmap_drift.py    --space 9-my-venture --strict   # CI
```

`roadmap_drift.py` reconciles claims against what the repos actually say —
epics pointing at issues closed as NOT PLANNED, `now` epics no task points at,
notes asserting a state older than 45 days, stray roadmap files declaring no
canonical source. One afternoon of it found five epics whose state the roadmap
had wrong.

**Caveat for a new venture:** `agent_readiness.py` hardcodes
`ROADMAP = 5-plur/roadmap.yaml` (it scans all spaces only for *which* have a
roadmap). The validate/render/drift trio take `--space` or `ROADMAP_SPACE`;
agent-readiness does not. Parameterise it or run its five checks by hand.

---

## Stage 6 — Roles and cadences: the workflow layer

This is where a venture stops being documents and starts running.

### Roles

Base archetypes live in `.datacore/templates/roles/*.base.md` — `ceo`, `cto`,
`cmo`, `cfo`, `cio`, `bizdev`, `operator`, `researcher`, `scout`, `dev` and its
variants (`dev.backend`, `dev.qa`, `dev.contracts`). The venture overlay goes at
`<space>/.datacore/roles/<role>.md`, and `role_loader.py` merges base + overlay
so an agent gets the shared archetype **and** the venture-specific focus.

A role entry in `venture.yaml` carries `description`, `cadences`,
`budget_authority`, and optionally `agent:` (a named agent instance — PLUR's
`cmo` is Data, its `cio` is Tris) and `autonomy_override:`. Extras round-trip,
so `decisions`, `receives`, `boundary`, `metrics` are all legitimate to add.
Write the **hard boundaries** in the description. PLUR's bizdev role reads:
"qualifies prospects, it never contacts them" — that sentence is load-bearing.

### Cadences

A cadence is a markdown file at `.datacore/templates/cadences/<name>.md`:

```markdown
---
cadence: hypothesis-review
role: ceo
frequency: weekly          # daily | weekly | monthly | quarterly
duration: 15min
tools: [plur_recall_hybrid, datacore.search]
---

## Objective
One sentence: what this cadence achieves.

## Steps
1. First concrete action

## Output
What to produce.

## Success Criteria
How to know it was done well.
```

Twenty-eight templates already exist (plus `_example.md`) — `budget-review`, `strategy-review`,
`hypothesis-review`, `competitor-scan`, `github-issue-triage`, `pr-review`,
`release-check`, `security-review`, `sprint-rollover`, `sprint-claim`,
`content-calendar`, `positioning-review`, `partnership-check` and more. **Look
before you write one.**

Reference it from the role in `venture.yaml`, and it is live:

```yaml
roles:
  ceo:
    cadences: {weekly: [hypothesis-review], monthly: [budget-review]}
```

One inconsistency to know about: `sprint-claim.md` declares
`frequency: hourly` in its frontmatter, which is **not** one of the four engine
frequencies. PLUR wires it under `cto.cadences.daily`. The frontmatter documents
the intended rhythm; the venture.yaml entry is what fires.

### How they fire

`cadence_engine.py` reads the roles dict, compares against
`<space>/.datacore/state/venture/cadence-log.yaml`, and returns what is overdue
within `FREQUENCY_WINDOWS` (1/7/30/90 days). `venture_heartbeat.py` ticks every
30 minutes:

```bash
python3 .datacore/modules/ventures/lib/venture_heartbeat.py --once --venture=my-venture
```

Two modes, switched by environment and **not** migratable after the fact:

| `DATACORE_CADENCE_PROPOSALS` | Behaviour |
|---|---|
| `0` (default) | Generator keeps `:AI:` tags, writes to `org/next_actions.org`; heartbeat executes via the Claude CLI directly. |
| `1` | Cadences enter `org/inbox.org` **without** an AI dispatch tag or fabricated approval; the heartbeat captures work instead of launching the model. |

Safety rules that hold in both modes, and are worth internalising because they
are the difference between a cadence log and a fiction:

- **A successful process or model string is not completion.** Actual task or
  artifact evidence is required before the cadence record advances
  (`cadence_completion.py`). A legacy inline heartbeat can run, but its text
  alone does not advance history.
- Machine-generated tasks keep their true `ORIGIN` and never manufacture
  `APPROVED_BY`. Restoring an AI tag does not forge human authorization.

Use `heartbeat.triggers` to control what wakes the venture at all. PLUR set
`triggers: [cadences]` on 2026-09-04 — new GitHub issues, inbox size and
hypotheses deliberately do **not** wake it.

**Start with one cadence.** PLUR cleared every cadence on its CEO, CTO and CMO
roles on 2026-09-04 and rebuilt them one at a time; only the CIO's were live
during the rebuild. A venture that declares twelve cadences on day one produces
twelve overdue flags on day two and teaches you to ignore the section.

---

## Stage 7 — Sprints and execution

Cadences keep a venture alive. Sprints are how a *project inside* it ships.

### The sprint object

```
<project>/sprints/
  sprint.schema.json
  <sprint-id>/
    sprint.yaml        # backlog, claims, hitl_log — the operational source
    canvas.md          # strategic frame for THIS sprint
    retro.md           # written at close
```

Fourteen required root keys, all of them: `sprint_id`, `project`, `status`,
`cadence`, `dates`, `goal`, `okr_links`, `facilitator`, `miles_routing`,
`backlog`, `stretch`, `hitl_log`, `claims`, `done_when`. Status vocabulary is
`planning | active | review | retro | closed` — `archived` is deliberately
absent, which is why `sprints/_archive/` is excluded from validation.
`dates.retro` and `carryover` become required at `status: closed`, and carryover
must be an explicit list, not prose.

Validate exactly what CI validates:

```bash
python scripts/validate_sprint.py $(git ls-files 'sprints/*/sprint.yaml' 'sprints/*.yaml' \
  | grep -v '^sprints/_archive/')
```

### The loop

1. **`sprint-rollover`** (weekly cadence, `cto`) — when a new ISO week starts
   with no sprint on the books, carries over unfinished items, drafts the next
   sprint in `planning`, and queues a `sprint_go` decision. **It never activates
   a sprint.** Idempotent: exits silently if a `planning` or `active` sprint
   already exists for the week.
2. **`/sprint-start`** — the kickoff ceremony. Reads `CANVAS.md`, walks the
   backlog, takes last adjustments, then flips `status: planning → active` and
   commits. (Activation can also fall to the decision pipeline's `default_at`
   SLA if GO never arrives — PLUR's W27 activated that way.)
3. **`sprint_sync.py`** — projects the active sprint into the agent queue.
4. **`sprint-claim`** (daily cadence) — an agent claims one ready item and ships it.
5. **`/retro`** — retrospective, HITL log fully classified, engrams captured.

### The three levels — the most expensive thing to get wrong

```
1. A task in next_actions.org             — exists
2. ...tagged :AI:                         — is a CANDIDATE
3. ...AND referenced from nightshift.org  — RUNS
```

`build_queue` keeps only entries whose file is `nightshift.org`. On 2026-09-04
the fleet had 261 tasks at level 2 and the runner executed 2. The system printed
the warning on every run and it read as noise.

`sprint_sync.py` writes all three levels, and the direction is one-way:

```
in sprint + claimed by an agent  →  tagged :AI:, entry in the queue
not in sprint                    →  :AI: removed, queue entry removed
                                    (the task itself always stays)
```

**`:AI:` is a projection of an active sprint, never a hand-applied label** —
`sprint_sync --apply` strips any tag it did not put there. Removing the tag does
not delete or deprioritise: it says "not this sprint", which is what a sprint
means.

```bash
python3 .datacore/lib/sprint_sync.py --space 9-my-venture --active          # dry run
python3 .datacore/lib/sprint_sync.py --space 9-my-venture --active --apply
```

It **refuses** sprint items claimed by an agent that lack `roadmap`,
`milestone`, `surface` and `acceptance`. That refusal is the feature: an
underspecified item is caught while a human is still looking at the sprint,
instead of at 02:00 by an agent that cannot act on it.

### Where the record lives

Three records per task, and only one of them can prove anything:

| | where | attested |
|---|---|---|
| org-mode | `<space>/org/*.org` | no — working state, `NIGHTSHIFT_*` telemetry, CLOCK |
| ledger | `<space>/.datacore/events/<actor>.jsonl` | **yes** — sha256 chain, signed, sealed |
| agent stream | `~/.datacore/cos/agent-stream/events-<date>.jsonl` | no — exec id, duration, tokens |

For org-routed work the ledger is a birth-and-death register: `item.create`,
`item.update`, `item.dismiss`. It never sees the task claimed, executed or
evaluated. "Who did this task, when, and at what cost" is answerable from
evidence, but not from the attested chain. Know that before you rely on it.

---

## Stage 8 — Make it visible

A venture nobody sees is a venture that quietly stops.

- **`/today`** — the ventures `today-hook` (priority 80, section "Venture
  Portfolio") reports per-venture cadence status, budget and hypothesis health
  for every space with a `venture.yaml`, and generates org tasks for overdue
  cadences.
- **`venture-monitor`** agent, trigger `:AI:venture:monitor:` — the daily
  heartbeat narrative.
- **MCP read tools** (open-source surface, read-only through the Python
  evidence readers): `datacore_ventures_list_ventures`,
  `datacore_ventures_read_venture`, `datacore_ventures_list_hypotheses`,
  `datacore_ventures_venture_status`.
- **App mutation tools** (`ventures_create_venture`, `ventures_update_venture`,
  `ventures_add_hypothesis`, `ventures_archive_venture`, …) route through the
  daemon with safety gates, checkpoints, locking and event emission. The split
  is deliberate — reads are free, writes are gated.
- **Budget** — `budget_tracker.py` keeps a plaintext monthly ledger at
  `.datacore/state/venture/budget-ledger.yaml`. A budget check describes a
  snapshot; it does not authorise a payment or reserve funds.

---

## Stage 9 — Stage transitions

Move `stage:` on evidence, along the allowed edges only:

| To | What should be true |
|----|---------------------|
| `discovery` | space exists, thesis written, first hypothesis on the board |
| `validation` | a hypothesis is `active` with a metric, window and budget |
| `growth` | at least one hypothesis `validated`; roadmap exists and validates |
| `maturity` | cadences complete with evidence without prompting; drift check clean |
| `archived` | reversible — `archive_venture` stops cadences; `restore_venture` returns it to `discovery` |

Archiving is destructive enough that the tool description says to always confirm
with the user first. It is not deletion, and the venture keeps its number.

---

## The minimum that is actually a venture

The nine stages describe the full shape. **Day one is three things:**

1. `venture.yaml` that loads — name, description, stage, one role, a budget.
2. `hypotheses.yaml` with one hypothesis carrying a metric, a window, a budget.
3. One cadence on that role that reviews it.

That is a venture. It wakes on the heartbeat, appears in `/today`, and has one
falsifiable claim under test. Everything else — intent graph, brand system,
roadmap, sprint train — earns its place when there is something to point it at.

The evidence in this tree is one-directional on this point: the failures are
structures built before there was work to put in them. Sprints 2 through 6 of
PLUR Enterprise have no retro written. Forge's hypothesis board has four
hypotheses proposed in April that went cold in July without a single experiment.
PLUR cleared every cadence on three roles and rebuilt them one at a time. None
of those were failures of the framework; they were the cost of installing more
of it than the venture was ready to carry.

---

## Checklist

```
[ ] 0  /office-hours design doc exists; triggering decision captured
[ ] 1  venture.yaml loads; budget invariant holds; stage is honest
[ ] 2  org/intents.org seeded, DRAFT tags dropped, goals carry :SUCCESS:
[ ] 2  intent_tasks.py run — unplaced tasks reviewed, not suppressed
[ ] 3  hypotheses.yaml has one active hypothesis with metric + window + budget
[ ] 4  positioning + voice written; /scaffolding-audit gaps reviewed
[ ] 5  roadmap.yaml validates; every item serves a resolvable INTENT_ID
[ ] 5  roadmap_drift.py --strict wired into CI
[ ] 6  one role, one cadence, one completed run with real evidence
[ ] 6  heartbeat.triggers set deliberately
[ ] 7  sprint.yaml validates against sprint.schema.json
[ ] 7  sprint_sync.py --apply runs; nightshift.org exists in the space
[ ] 8  venture appears in /today; budget ledger initialised
```

---

## Known gaps in the framework itself

Found while writing this. Each is a real defect, not a caveat:

1. `.datacore/templates/space/venture.yaml.template` writes `roles:` as a list;
   `VentureConfig` requires a dict. The template cannot produce a loadable file.
2. `comms/module.yaml` declares a `brand-positioning` command; the file
   `commands/brand-positioning.md` does not exist.
3. `agent_readiness.py` hardcodes `5-plur/roadmap.yaml`. A second venture with a
   roadmap cannot be checked with it.
4. `sprint-claim.md` declares `frequency: hourly`, which the cadence engine does
   not support.
5. The ventures module `CLAUDE.base.md` lists `cadence-log.yaml` and
   `budget-ledger.yaml` at the space root; both now live under
   `.datacore/state/venture/`.

---

*Sources: `.datacore/modules/ventures/` (loader, cadence engine, heartbeat,
commands), `.datacore/lib/intent_*.py`, `.datacore/lib/roadmap_*.py`,
`.datacore/lib/sprint_sync.py`, `.datacore/specs/task-lifecycle-and-ledger-coverage.md`,
`~/.claude/skills/roadmap-build/SKILL.md`, `datacore-app/daemon/datacored/adapters/spaces.py`,
and the seven ventures that already run.*
