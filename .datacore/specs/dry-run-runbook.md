# Dry run — clean machine, founder as new user

Written 2026-09-18. This is validation instance **(a)** from the CoS product
proposal §9: *clean second box, Gregor-as-new-user, empty data, measuring
time-to-first-briefing without the benefit of an existing installation.*

Sunday's external user is instance (b). Monday's workshop is the first group.
This run is what stops Sunday being the first time anyone finds out.

**The three success criteria are the proposal's own, unchanged:**

| | Criterion |
|---|---|
| 1 | Install completes in **≤ 30 minutes** |
| 2 | The **first briefing** is produced |
| 3 | **Zero** interventions that a workshop attendee could not have done themselves |

Criterion 3 is the one that matters. If you fix something by editing a file or
sshing anywhere, the run has failed even if the briefing appears — because on
Monday you will not be sitting at twelve laptops.

---

## Pre-flight — DONE, and one thing that is not

### Shipped 2026-09-18 — both on `main`, which is what a fresh install clones

Modules are installed with a plain `git clone <repo>` — **no tag, no branch
flag** — so the default branch at HEAD is what arrives. There is no pinned
release to cut.

| Repo | Commit | What |
|---|---|---|
| `datacore-one/datacore-ventures` | `3189bbc` | `venture_init.py`, `venture_doctor.py` |
| `datacore-one/datacore` | `eb6c2dd` | the space template, which could never produce a loadable venture |
| `datacore-one/datacore` | `f238b92` | the deck and spec renderers |

### The one that is NOT solved: ventures is not in the installer's catalog

`datacore init` installs from `AVAILABLE_MODULES` in the CLI
(`src/lib/module.ts`). That catalog has **ten** modules — nightshift, health,
crm, meetings, mail, news, slides, trading, telegram, campaigns — and
**`ventures` is not one of them.** So a clean install has no ventures module,
and the two scripts above are not on the machine no matter what is on `main`.

Fixing the catalog means publishing a new CLI to npm (currently 2.0.0). That is
not a thing to do the day before a workshop.

**So the venture step gets an explicit install command, in the runbook and on
the slide:**

```bash
datacore module install https://github.com/datacore-one/datacore-ventures
python3 -m pip install -r ~/Data/.datacore/modules/ventures/requirements.txt
```

**The pip line is not optional.** `runModulePostInstall()` — the function that
reads a module's `requirements.txt` — is called only from `datacore init`, never
from `datacore module install`. So a module added after setup is cloned without
its Python dependencies, and `venture_init.py` dies on `import pydantic` at the
worst possible moment. The module needs `pyyaml`, `pydantic>=2`, `ruamel.yaml`.

Verify both before going further:

```bash
ls ~/Data/.datacore/modules/ventures/lib/venture_init.py
python3 ~/Data/.datacore/modules/ventures/lib/venture_init.py --list-cadences | head -3
```

The second is the real test: it exercises the imports. If it prints cadence
names, the venture step will work.

> Second post-Monday fix, with the catalog one: make `datacore module install`
> run the post-install step. A module that installs without its dependencies is
> a trap for every user who adds one later, not just this workshop.

> Add `ventures` to the CLI catalog and publish 2.1.0 **after** Monday. Doing it
> before means the workshop is the first run of an unreleased installer.

### Still to do, by you

- **Use the clean machine's own GitHub account and own Claude subscription.**
  Reusing yours hides the two most likely workshop failures: authentication, and
  a missing subscription.

## The run — on the clean machine

Record the wall-clock time at each step. A stopwatch is the instrument; the
30-minute criterion is meaningless measured by feel.

| # | Step | Command | Record |
|---|---|---|---|
| 0 | Start the clock | — | `T0` |
| 1 | Claude Code installed and signed in | — | minutes, and every prompt it asked |
| 2 | Install | `curl -fsSL https://datacore.one/install \| bash` | minutes; did it install Node itself? |
| 3 | Answer the wizard | (interactive) | every question, verbatim |
| 4 | Dependencies | `datacore doctor` | anything not green |
| 5 | Open it | `cd ~/Data && claude` | did MCP memory connect? |
| 6 | **First briefing** | `/today` | **`T1` — this is time-to-first-briefing** |
| 7 | A second space | `datacore space create` | minutes |
| 7b | **Install ventures** | `datacore module install …/datacore-ventures` then `pip install -r …/ventures/requirements.txt` | did `--list-cadences` print? |
| 8 | Make it a venture | `venture_init.py --space … --belief … --metric …` | did it write and verify? |
| 9 | Check the venture | `venture_doctor.py --space …` | required steps passing: ?/4 |
| 10 | Schedule it | `datacore cron install` | did it install? |
| 11 | **Next morning** | — | did a briefing arrive unasked? |

Step 11 is a separate day and cannot be rehearsed on Sunday morning. Run this
dry run **today or tomorrow**, so step 11 has a night to happen in.

### The exact venture command, so it is not improvised in the room

```bash
python3 ~/Data/.datacore/modules/ventures/lib/venture_init.py \
    --space 1-example --name "Example" \
    --belief "Freelance designers will pay for a weekly brief" \
    --metric "5 paying users within 30 days" --window 30 --budget 40 --dry-run
```

Drop `--dry-run` to write. Then:

```bash
python3 ~/Data/.datacore/modules/ventures/lib/venture_doctor.py --space 1-example
```

**Expected:** `required steps passing: 4/4`. Anything less, read the detail line —
it names the defect and the step.

---

## Known-rough, with the workaround decided in advance

Decide these now, not in front of someone.

| Rough edge | Workaround for Sunday/Monday |
|---|---|
| No graphical installer | The deck says so on the "what is still rough" slide. Lead with it rather than apologise later. |
| `datacore init` needs Claude Code already working | Make it a prerequisite on the invite, not a step in the room. |
| Venture creation is a python command, not a wizard | Put the exact command on a slide or in a pasteable gist. Do not type it from memory. |
| Cadence names must match an installed template | `venture_init.py --list-cadences` shows the 28. Pick from them. |
| Sub-daily cadences (`every_4h`) fail validation | Do not offer them. The engine supports daily / weekly / monthly / quarterly only. |
| A VPS is step two | Explicitly out of scope for both days. Nobody provisions a server in a workshop. |

---

## What to write down

One file, written **during** the run, not after:

```
2-datacore/1-tracks/ops/dry-run-<date>.md
```

For each failure: what you typed, what happened, and — the important one —
**whether an attendee could have recovered alone.** That last column is what
converts this run into a Monday go/no-go.

At the end, three numbers: `T1 - T0`, interventions required, required steps
passing on the venture.

---

## Go / no-go for Monday

- **Go** — install under 30 minutes, a briefing appeared, venture doctor 4/4, and
  every failure was recoverable by the person at the keyboard.
- **Go with a script change** — a failure recurred but has a known one-line
  workaround. Put it on a slide before Monday.
- **No-go on the venture step** — install works, venture creation does not. Run
  Monday as install-and-briefing only, and book the venture session separately.
  This is a perfectly good workshop and a much better one than a stuck room.

The last option is a real outcome, not a fallback to be embarrassed about. Decide
it Saturday rather than discovering it Monday.
