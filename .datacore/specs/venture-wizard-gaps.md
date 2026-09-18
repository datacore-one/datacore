# What a conversational venture wizard still needs

Companion to `venture-creation-playbook.md`. That document describes the nine
stages as they are performed today — largely by hand, with a different tool per
stage and several stages having no tool at all. This one lists the code that has
to exist before `/create-venture` can walk someone through all nine in
conversation, inside Claude Code, without the datacore-app daemon running.

Scope is **creating a venture**. Nothing here is about auditing or repairing the
ones that already exist.

---

## The shape of the wizard

Follow `/create-module` — a thin conversational command that gathers intent and
delegates to an agent. Three new files, none of them large:

| File | Role |
|------|------|
| `.datacore/commands/create-venture.md` | The conversational surface. Intent → stage selection → hand off. |
| `.datacore/modules/ventures/agents/venture-creator.md` | Does the work, one stage at a time, with the user answering between stages. |
| `.datacore/state/venture/<slug>/setup.yaml` | Resume state — which stages are done, what was answered. |

Two properties the command must have, because venture setup is not a single
sitting:

1. **Resumable.** Nine stages will not finish in one conversation. Re-running
   `/create-venture` on an existing slug must pick up where it stopped, not
   start over and not overwrite.
2. **Stage-skippable.** A `discovery`-stage venture should be told to stop after
   stage 3. The playbook's "minimum that is actually a venture" is the default
   path; stages 4–7 are offered, not assumed.

Everything below is what those three files would need underneath them, and do
not have.

---

## Blockers — the wizard cannot run without these

### 1. A local venture scaffolder

**Missing:** `.datacore/lib/venture_scaffold.py`

`scaffold_space()` already does exactly the right thing — next free numeric
prefix that never reuses a gap, whole tree built in a temp dir, `load_venture`
validated before anything lands, atomic rename, collision refusal. It is the
best piece of code in this whole path.

It lives at `datacore-app/daemon/datacored/adapters/spaces.py` and imports
`..states`, so it is reachable only through a running daemon. A wizard in Claude
Code cannot call it.

**This is a lift, not a rewrite.** Move the scaffolder and `TEMPLATE_REGISTRY`
into the ventures module (it is the ventures module's job), expose a CLI, and
have the daemon adapter import it. One implementation, two callers.

```bash
python3 .datacore/lib/venture_scaffold.py --name "The Practice" \
    --stage discovery --template blank --dry-run
```

`--dry-run` already exists in the daemon version and returns the planned file
list. That is precisely what the wizard shows the user before writing.

### 2. A setup doctor

**Missing:** `.datacore/lib/venture_doctor.py`

The single highest-value item on this list. It answers, for one venture, which
of the nine stages are complete — and it is three things at once:

- the wizard's **progress display** between stages,
- the wizard's **resume mechanism** (recompute rather than trust `setup.yaml`),
- the **exit test** for each stage, so "done" is machine-judged rather than
  declared.

`venture_status.py` does not do this. It reports cadence, budget and hypothesis
health for ventures that already load — runtime health, not setup completeness,
and it cannot run at all before stage 1 finishes.

```
$ python3 .datacore/lib/venture_doctor.py --space 9-practice
  1  constitution   FAIL  no venture.yaml
  2  intent graph   n/a   (blocked by 1)
  ...
```

Every check must be mechanical. A stage that needs a human to judge whether it
is done is a stage the wizard will stall on.

### 3. A cadence catalogue

**Missing:** `.datacore/lib/cadence_catalog.py`

There are 28 cadence templates and no way to ask a question of them. The only
reader, `load_cadence_template()`, takes a name, strips the frontmatter and
returns the body — or `None`. So the declared `role` and `frequency` are
unreadable, and a wizard cannot offer "here are the cadences that suit a CEO."

Two functions:

- `list(role=None, frequency=None)` — parse frontmatter across the templates dir.
- `resolve(name)` — does this name have a template, yes or no.

The second closes a real hole. `RoleConfig.supported_cadences` validates the
frequency key and rejects duplicates, but **never checks that a cadence name
resolves to a template**. A typo passes validation, lands in `venture.yaml`, and
then quietly never fires. Wire `resolve()` into the validator and the typo dies
at write time.

### 4. A write path for the intent graph

**Missing:** `.datacore/lib/intent_node.py`

`intent_graph_scaffold.py` seeds `intents.org` from `venture.yaml` and
deliberately leaves every goal without `:SUCCESS:` — an invented metric reads as
real and is worse than a blank. Correct, and it means the wizard's job at stage
2 is to *elicit* those criteria and write them in.

There is no API for that. The options today are hand-editing org text, which the
house rule forbids, or `--force`, which rewrites the whole file and discards the
answers already given.

```bash
python3 .datacore/lib/intent_node.py --space 9-practice \
    --id north-star-sessions --set SUCCESS "..." --clear-tag DRAFT
```

Build it on org-workspace (`set_property`, `NodeView`), which already handles
properties, multiline values and IDs. This is glue, not a parser.

---

## Needed for the stages beyond the minimum

### 5. Roadmap init and schema

**Missing:** `.datacore/lib/roadmap_init.py` and `.datacore/schemas/roadmap.schema.json`

`roadmap_validate.py`, `roadmap_render.py` and `roadmap_drift.py` all take
`--space` and all assume a `roadmap.yaml` already exists. Nothing creates the
first one, and the rules it must satisfy live only inside the validator's Python.

`roadmap_init.py --space <s>` should write a minimal file that passes its own
validator on the first run: `goals` seeded from the venture's north star,
`north_star` block, `items: []`, and the header comment carrying the rules
(outcome-level only; every item `serves` a resolvable INTENT_ID; `gate` is a
condition never a date; `shipped: false` required on public claims).

`.datacore/schemas/` does not exist yet. It should — see item 7.

### 6. `agent_readiness.py` takes a `--space`

**Missing:** parameterisation. One line of real change.

It hardcodes `ROADMAP = REPO / "5-plur" / "roadmap.yaml"` while its three sibling
tools all accept `--space`/`ROADMAP_SPACE`. A second venture with a roadmap
cannot be checked with it, so the wizard cannot tell the user whether the
roadmap it just helped write is agent-executable.

### 7. Sprint schema and validator, promoted out of one project

**Missing:** `.datacore/schemas/sprint.schema.json`, `.datacore/lib/sprint_validate.py`,
`.datacore/lib/sprint_init.py`

`sprint.schema.json` and `validate_sprint.py` exist only inside
`5-plur/2-projects/enterprise/`. The fourteen required root keys, the
`planning|active|review|retro|closed` vocabulary, the rule that `dates.retro`
and `carryover` become required at `closed` — all of it is correct and all of it
is trapped in one project's `scripts/`.

A second venture starting its first sprint has nothing to validate against.
Promote the schema and validator; add `sprint_init.py` to write sprint 1 from
the roadmap items at `horizon: now`.

### 8. A brand brief scaffolder, and the missing comms command

**Missing:** `.datacore/modules/comms/commands/brand-positioning.md`

`comms/module.yaml` declares this command under `provides.commands`. The file
does not exist. The agent works via the `:AI:comms:brand:` tag, but the wizard
cannot invoke a command that was never written.

**Also missing:** something that turns the agent's answers into files at the
paths `SCAFFOLDING.base.md` specifies — `1-tracks/comms/positioning/`,
`1-tracks/comms/brand/voice.md`, `3-knowledge/pages/_core/`. Today the agent
produces prose and a human decides where it lands, which is why two ventures
that both did stage 4 have it in two different shapes.

### 9. A role overlay scaffolder

**Missing:** `.datacore/lib/role_overlay.py` (or a flag on the scaffolder)

`role_loader.py` merges `.datacore/templates/roles/<role>.base.md` with
`<space>/.datacore/roles/<role>.md`. The base archetypes exist for all twelve
roles; nothing writes the overlay. The wizard should ask the two questions that
matter — what this role owns here, and what it must never do — and write the
stub. PLUR's bizdev boundary ("qualifies prospects, it never contacts them") is
the example of why the second question is not optional.

---

## Fix while passing

### 10. The stale space template

`.datacore/templates/space/venture.yaml.template` writes `roles:` as a list;
`VentureConfig` requires a dict. The template cannot produce a loadable file.
Once item 1 exists the template is the fallback path, so it has to be correct —
or deleted, and the scaffolder made the only way in.

### 11. `sprint-claim.md` declares an unsupported frequency

Its frontmatter says `frequency: hourly`; the engine supports only `daily`,
`weekly`, `monthly`, `quarterly`. Item 3's `resolve()` should check the
frontmatter frequency against `FREQUENCY_WINDOWS` and fail this one loudly.

### 12. Ventures `CLAUDE.base.md` points at moved files

It lists `cadence-log.yaml` and `budget-ledger.yaml` at the space root; both now
live under `.datacore/state/venture/`. The wizard's own context would inherit
the wrong paths.

---

## Build order

Dependencies, not priorities. Nothing in the second row is worth building before
the first row works.

```
1 venture_scaffold  ──┬── 2 venture_doctor ──── /create-venture + venture-creator
                      │                            (minimum-venture path works here)
3 cadence_catalog  ───┤
4 intent_node      ───┘

5 roadmap_init ── 6 agent_readiness --space ── 7 sprint schema + init
8 brand command + scaffolder      9 role_overlay
10, 11, 12 — fix in the same commit as whatever touches them
```

Items 1–4 plus the three wizard files get the **minimum that is actually a
venture** — constitution, one hypothesis, one cadence — fully conversational.
That is the milestone worth shipping first; 5–9 extend the same wizard to the
stages a venture only reaches later, and every one of them is optional at
`discovery` stage.

### Rough sizes

| Item | New code | Character of the work |
|------|----------|----------------------|
| 1 venture_scaffold | ~50 lines + a move | lift out of the daemon, add CLI |
| 2 venture_doctor | ~200 lines | new, nine mechanical checks |
| 3 cadence_catalog | ~80 lines | new, frontmatter parse + validator wiring |
| 4 intent_node | ~120 lines | glue over org-workspace |
| 5 roadmap_init | ~120 lines | new, plus extracting rules into a schema |
| 6 agent_readiness | ~10 lines | parameterisation |
| 7 sprint schema/init | ~100 lines + a move | promote, then init from roadmap |
| 8 brand | ~150 lines | one command file + a writer |
| 9 role_overlay | ~60 lines | new, two questions and a template merge |

Only items 2, 3, 4 and 5 are genuinely new logic. Three of the nine are moving
code out of a place that traps it — the daemon, one project's `scripts/`, one
validator's hardcoded path — which is the cheapest kind of work on this list and
the reason the wizard is closer than it looks.
