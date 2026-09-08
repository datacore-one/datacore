# Datacore — shared language

The words this installation uses, and the ones it deliberately does not. When a
term here has an _Avoid_ line, the listed synonyms are drift: they read as new
concepts and cost a reader the work of discovering they are not.

Scope: the root repo `datacore-one/datacore` and every space under it. Module-
specific vocabulary belongs in that module's own `CLAUDE.base.md`.

---

## Structure

**Space**
A top-level `[N]-[name]/` directory that is its own git repo, with its own
`CLAUDE.md`, org files, knowledge base and journal. The unit of convergence —
transport operates on a space, never on a file.
_Avoid_: workspace, vault, folder (a space is not merely a directory)

**Root repo**
`~/Data` itself, published as `datacore-one/datacore`. Holds `.datacore/`,
the modules, the libs and the DIPs. Changes to it go through a branch and a PR;
spaces commit on `main`.
_Avoid_: main repo, the Datacore folder, the parent repo

**Module**
A self-contained package under `.datacore/modules/<name>/` with a `module.yaml`
manifest, adding agents, commands, tools and context to one domain. A module
ships **code, never the user's data**.
_Avoid_: plugin (reserved for Claude Code plugins), extension, package

**DIP**
Datacore Improvement Proposal — a numbered spec in `.datacore/dips/` defining a
system pattern. `Implemented`/`Accepted` requires owner ratification;
auto-generated DIPs stay `Draft`.
_Avoid_: RFC, ADR, proposal doc

**Layered context**
The `.base.md` → `.space.md` → `.local.md` composition (DIP-0002) that produces
a gitignored `.md`. `.base.md` is public, `.local.md` is private.
_Avoid_: context inheritance, config layering, overlay

**Track**
A `[space]/1-tracks/<name>/` directory organising work by department (dev,
product, comms, growth). Where durable working documents live.
_Avoid_: department folder, workstream directory

**Focus mode**
The state when a session starts inside `[space]/2-projects/[project]/`: a
lightweight shim replaces the full context. Detected by
`focus_mode.py detect`, which reports `focus`, `full` or `none`.
_Avoid_: project mode, scoped session

## Memory

**Engram**
One durable unit of memory in PLUR: a statement, its domain, a confidence and a
commitment level. The thing that survives the quarter.
_Avoid_: memory (too broad), note, fact record

**PLUR**
The memory engine and its MCP server. Global — one store, many projects; multi-
project separation is by **scope**, not by separate installs.
_Avoid_: the memory server, the engram database

**Scope**
Which store an engram is written to, chosen **per engram by its content** —
a team store for shared engineering knowledge, the local/default store for
personal or project-specific ones.
_Avoid_: namespace, bucket, store selection

**Auto-memory**
The Claude Code memory directory and its `MEMORY.md` index. A *different*
system from PLUR: loaded into the system prompt every session, so its index
lines are a disclosure surface. Holds state with an expiry date; anything true
in six months belongs in PLUR.
_Avoid_: local memory, Claude memory, the memory folder

**Learning sweep**
The nightly batch (05:20) that reads the day's archived session transcripts and
writes engrams. Replaced per-session learning agents, which cost ~112k output
tokens per engram and never terminated.
_Avoid_: learning pass, the classifier, nightly learning agent

## Ledger and transport

**Ledger**
The append-only event log per space under `.datacore/events/<actor>.jsonl`
(DIP-0034). Hash-chained: each event carries `seq`, `prev` and `hash`.
_Avoid_: event store, audit log, the journal (the journal is prose, and separate)

**Actor**
The identity an event is attributed to (DIP-0044) — `mac`, `nightshift`,
`winston`. One `.jsonl` file per actor, which is what lets independent machines
append without conflicting.
_Avoid_: agent (an agent is a role; an actor is an identity), host, writer

**Converge**
Receive other machines' facts and publish yours:
`ledger_transport.py converge --space <space>`. The **only** sanctioned way to
sync a space.
_Avoid_: sync, pull, push, `git pull --rebase --autostash` (which loses work)

**Autosave**
The commit a converge makes of a dirty tree before integrating, so nothing is
carried through a merge uncommitted. Visible as `ledger: autosave before converge`.
_Avoid_: stash, WIP commit

**Run branch**
The branch a nightshift run works on. Since datacore#148 a run writes events to
`<actor>-run-<date>.jsonl` so that one append-only chain is never extended on
two branches at once — the failure that stranded 7 branches on 2026-09-08.
_Avoid_: nightshift branch, job branch

**Projection**
Generating an org file from ledger state. A projected file is read-only
(`chmod 444`); `inbox.org` is exempt in every phase and stays the capture surface.
_Avoid_: rendering, materialising, export

**Phase 1**
A space whose `next_actions.org` is projected from the ledger rather than
hand-edited. Write to `inbox.org` instead; the projector refuses direct writes.
_Avoid_: ledger mode, migrated space

## GTD

**Inbox**
`[space]/org/inbox.org` — the single capture point, and the only org file every
command may write to. Returned to clean, not accumulated.
_Avoid_: capture file, todo list

**Continuation task**
A `:continuation:`-tagged task carrying a `BOOTSTRAP` property, created by
`/continue --save` or `/wrap-up`, scheduled on the next working day. What
`/continue` looks for when resuming.
_Avoid_: follow-up task, resume task, carry-over

**BOOTSTRAP**
The property holding a self-contained prompt for the next session: what was
done, what remains, what blocks it. The thing that removes "where was I?".
_Avoid_: context blob, handoff note

**Rich Task Standard**
The property set defined in DIP-0009 Part 3.5 — `CONTEXT`, `KEY_FILES`,
`CURRENT_STATUS`, `ACCEPTANCE_CRITERIA`, `TOOLS`, plus `BOOTSTRAP` — that makes
a task executable by an agent without the original conversation.
_Avoid_: full task format, enriched task

**DONE_WHEN**
The property stating the observable condition that closes a task. Not a
description of the work — a test a reader can run.
_Avoid_: acceptance criteria (that is a separate, plural property), definition of done

**`:AI:` tag**
Marks a task for autonomous execution by nightshift, optionally qualified
(`:AI:research:`, `:AI:content:`). **`sprint_sync` is the only writer of this
tag** — never add or remove it by hand.
_Avoid_: delegation tag, agent tag

**Nightshift**
The overnight autonomous execution system, and the box it runs on. Executes
`:AI:`-tagged tasks on a schedule; its run logs carry the per-task token cost.
_Avoid_: the overnight agent, the server, batch mode

**Cadence**
A recurring obligation a venture owes on a schedule (DIP: ventures module).
Overdue cadences are a health signal, not a task backlog.
_Avoid_: recurring task, ritual, ceremony

## Knowledge

**Zettel**
An atomic concept note in `[space]/3-knowledge/zettel/`. Distinct from
`literature/` (a summary of a source) and `reference/` (an entry about a person
or company).
_Avoid_: note, atomic note, permanent note

**Datacortex**
The knowledge graph and semantic search over local knowledge and journals.
`datacore.search` reaches it. Does **not** search engrams — that is
`plur_recall`.
_Avoid_: the index, the vector store, RAG

**Artifact index**
`0-personal/notes/artifact-index-YYYY-MM.md` — the append-only monthly table
answering "when did I work on X and where is it?".
_Avoid_: file index, output log

## Operations

**Broker**
`creds.py` — the one way to obtain a credential:
`creds.py get <id> --consumer <who>`. Resolves the single declared location and
verifies it before returning. **Searching for credentials is the failure mode
the broker exists to prevent.**
_Avoid_: secret store, vault, env lookup

**Verdict**
The one label every entry in `feature-ideas.md` leaves review with: **ADOPT**,
**TRIAL**, **WATCH** or **DROP**. No idea leaves without one, and DROP is a
success.
_Avoid_: status, decision, disposition

**Pulse**
The single non-blocking 1-10 question `/wrap-up` asks at the start. An
unanswered pulse is a normal outcome and nothing may block on it.
_Avoid_: check-in, mood score

## Relationships

- A **Space** contains one **Ledger**, which holds **Events** written by **Actors**
- **Converge** is the only operation that moves a **Space** between machines
- A **Phase 1** space **projects** `next_actions.org` from its **Ledger**; its **Inbox** is never projected
- An **Engram** lives in PLUR under a **Scope**; **Auto-memory** is a separate system with different rules
- A **Continuation task** carries a **BOOTSTRAP** and follows the **Rich Task Standard**
- The **Learning sweep** reads archived sessions and writes **Engrams**

## Flagged ambiguities

- **"sync"** meant three different things: the retired `./sync` script, git
  push/pull, and ledger convergence. Resolved: convergence is **converge**;
  the other two are named explicitly as git operations. Do not reintroduce
  "sync" as a verb for spaces.
- **"park"** described `git stash push -u -m nightshift-park-<ts>`. That
  pattern lost work on conflict and was replaced by commit-onto-a-branch.
  Retired: do not use "park" for the current behaviour, which is **autosave**.
- **"rescue branch"** (`mac-rescue-<ts>`) belonged to the rebase-based
  transport that DIP-0046 retires. Historical only.
- **"memory"** was used for both PLUR engrams and the Claude Code auto-memory
  directory. Resolved: **Engram**/**PLUR** for one, **Auto-memory** for the
  other. Never bare "memory" in a routing instruction.
- **"agent"** was used for both a role (`journal-coordinator`) and a ledger
  identity. Resolved: the identity is an **Actor**.
- **"task"** vs **"delegation"**: an org task mirrored into the ledger carries
  an `org` block and `ledger_claim.py` skips exactly those, so a task written by
  a command can never be picked up by the fleet. If an agent should do it
  tonight, it is a **delegation** (an `:AI:` tag), not a task.
