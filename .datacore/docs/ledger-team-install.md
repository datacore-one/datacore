# The ledger for a team: install and runbook (profile A)

This page installs the event ledger for a team and says what to do when
something goes wrong. Follow it top to bottom the first time. Every command is
safe to run again.

**Profile A** is the smallest install: one machine, or several machines that
share one git remote; people only. It covers the ledger itself, `verify` and the
command line. There are no agents, no generated org files, no scheduled fleet
jobs and no sequencer.

Three rules hold everywhere on this page:

- **History is never rewritten.** No step edits or deletes an event. A wrong
  event is cancelled by a void record that is itself an event (section 8.3).
- **There are no exceptions files.** Nothing outside the ledger excuses an
  event; verify accepts the ledger and its void records, nothing else.
- **Signing is not switched on yet.** It comes later as one step, after the
  verifier judges single events. Until then the write gate (section 4) keeps
  hand-written lines out. Leave `DATACORE_LEDGER_SIGN` unset.

In the commands, `$DC` is where you put Datacore (for example `~/Data`),
`$LIB` is `$DC/.datacore/lib`, and `$SPACE` is the space folder (for example
`$DC/team`). Set them once per terminal:

```bash
DC=~/Data
LIB="$DC/.datacore/lib"
SPACE="$DC/team"
export DATACORE_ROOT="$DC"
```

## 1. What you need

- Python 3.10 or newer, and git.
- Two Python packages: `PyYAML` and `cryptography`.
- One login per person on the machine (each person's identity lives in their
  own home folder, `~/.datacore/identity.env`).
- For several machines: one git remote that everyone can push to, with one
  repository per space.

## 2. Install

Each step is run once per machine; step 2.3 is run by each person, in their own
login.

### 2.1 Get the code and the packages

```bash
git clone <your copy of the datacore repository> "$DC"
python3 -m pip install PyYAML cryptography
```

### 2.2 Ask the doctor what is missing

```bash
python3 "$LIB/ledger_cli.py" doctor --space "$SPACE"
```

Every line that starts with `MISSING` ends with `fix:` and the command that
fixes it. On a new machine it names your identity, the principals registry and
the space's ledger. `note` lines are not gaps. The doctor exits 0 only when
nothing is missing.

### 2.3 Initialise the space as yourself

Use a person's name, never a machine name (it becomes your log file,
`<name>.jsonl`). `--email` is the address you commit with; it is stored only as
a hash and lets verify check that your log is only ever committed by you.

```bash
python3 "$LIB/ledger_cli.py" init --space "$SPACE" --actor alice --email <alice's git email>
```

`init` does four things and changes nothing that is already there:

1. writes `DATACORE_ACTOR=alice` to `~/.datacore/identity.env` (it refuses if
   this login already declares someone else);
2. adds alice to `$DC/.datacore/registry/principals.yaml` as a person;
3. makes the space ready: `.datacore/events/`, the edit protocol marker, a space
   marker (so sync needs no registry entry) and `.datacore/state/` kept out of
   git;
4. if the space is a git checkout, wires Datacore's git hooks so every commit
   and push passes the write gate (section 4).

### 2.4 Declare your teammates

`principals.yaml` is private to the installation and is not shared through
git, so declare every person on every machine:

```bash
python3 "$LIB/ledger_cli.py" principals add --actor bob --kind human --email <bob's git email>
```

Then run the doctor again (2.2). It must say `nothing missing`.

## 3. Use it

```bash
python3 "$LIB/ledger_cli.py" append --space "$SPACE" --type item.create --payload '{"id": "t-1", "title": "First task"}'
python3 "$LIB/ledger_cli.py" append --space "$SPACE" --type item.claim --payload '{"id": "t-1"}'
python3 "$LIB/ledger_cli.py" append --space "$SPACE" --type item.complete --payload '{"id": "t-1"}'
python3 "$LIB/ledger_cli.py" items --space "$SPACE"
python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
```

`items` folds the history into the current state; `verify` checks every
writer's hash chain and prints `OK <files> files <events> events`.

## 4. Share it through one git remote

Skip this section on a single machine: a space with no remote is reported by
sync as `local` and is never touched.

```bash
export DATACORE_ACTOR=alice        # your name; see "Until the push guard" below
git -C "$SPACE" init -b main
git -C "$SPACE" remote add origin <url of the space's repository>
python3 "$LIB/ledger_cli.py" init --space "$SPACE" --actor alice
git -C "$SPACE" add -A && git -C "$SPACE" commit -m "Start the ledger"
python3 "$LIB/ledger_transport.py" converge --space "$SPACE" --root "$DC" --line
```

Run `init` after `git init` (or again): that is what wires the write gate. The
gate refuses, at commit and at push, any ledger line the ledger did not write: a
hand-edited line, a line in someone else's log, an edited old line, a time far
in the future, a broken chain.

`converge` commits local changes, merges the remote (never rebases) and pushes.
The first converge into an empty remote publishes the space (`first publish`).
Run it whenever you want to share, or on a timer:

```bash
# crontab -e, every 15 minutes (adjust the paths and the name)
*/15 * * * * DATACORE_ROOT=$HOME/Data DATACORE_ACTOR=alice python3 $HOME/Data/.datacore/lib/ledger_transport.py sync --root $HOME/Data --quiet
```

**Until the push guard reads your identity file** (pending, 2026-10-04): the
pre-push check that each person pushes only their own log still takes the writer
from `DATACORE_ACTOR` or, failing that, from the machine's name. Set
`DATACORE_ACTOR` to your name wherever you push or converge: in your shell
profile (`export DATACORE_ACTOR=alice` in `~/.profile`) and in the cron line
above. Otherwise your first push is refused as "<machine name> modified another
actor's event log".

`sync` converges every space under the root that has a space marker; it needs
no registry file.

## 5. Check it every day

Verify each space, not the root folder: the root's own `.datacore/events/` is
local telemetry, so verifying it proves nothing about the spaces.

```bash
# crontab -e, daily at 07:20 (one line per space)
20 7 * * * DATACORE_ROOT=$HOME/Data python3 $HOME/Data/.datacore/lib/ledger_cli.py verify --space $HOME/Data/team >> $HOME/.datacore/ledger-verify.log 2>&1 || echo "ledger verify failed for team" | mail -s "ledger" <your address>
```

## 6. Never delete `ledger/*` branches

Branches named `ledger/<writer>` on the remote carry a writer's log while it
could not be merged into `main`. Every converge merges them back. Deleting one
before it is merged loses that writer's events everywhere. Leave them; they are
small. If one looks stale, run a converge and check that its commits are in
`main`:

```bash
git -C "$SPACE" fetch origin
git -C "$SPACE" branch -r --list 'origin/ledger/*' --no-merged origin/main
```

An empty answer means every `ledger/*` branch is merged. Even then, ask the
person who owns the installation before deleting anything.

## 7. Turning signing on (not yet)

Signing is a later step for the whole team at once, not something one person
switches on. When it comes, the doctor will name every writer without a key.
Until then, a lost key file stops nothing (section 8.4).

## 8. When something goes wrong

After any procedure below, check the space:

```bash
python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
python3 "$LIB/ledger_cli.py" doctor --space "$SPACE"
```

### 8.1 StaleLogError: the log was rewound

You see an append refused with `StaleLogError: <writer>.jsonl ends at seq N but
this machine already wrote seq M`, and `ledger_cli.py stopped --space $SPACE`
lists the log.

This machine remembers writing further than the log file now goes: a checkout,
merge or restore took events away, or two copies forked. Appending anyway would
reuse a sequence number, so the ledger stops that log until a person decides.
Only the person who owns the installation runs the fix; nobody works around the
refusal.

Follow the tested procedure in `.datacore/docs/recovery.md`, section "Stale
log", with its `SPACE` and `LIB` lines set to your paths. In short: look for a
longer copy of the log in every history and on every teammate's machine first
(`ledger_restore_prefix.py --find`) and restore it if one exists; only if none
exists, move the sequence mark aside (never delete it); then merge, and verify.

### 8.2 Push refused: ledger fork

You see a converge or push refused with `push REFUSED — ledger fork against
origin/main`.

Two copies of one writer's log disagree about what one sequence number holds.
The push is refused so the fork never reaches the remote; nothing was lost, and
the remote is unchanged. The remote's copy stands. Keep yours as evidence, take
the remote's log, and enter again, as new events, whatever existed only here:

```bash
git -C "$SPACE" fetch -q origin
git -C "$SPACE" update-ref "refs/fork-evidence/$(date +%Y%m%d)" HEAD
git -C "$SPACE" checkout origin/main -- .datacore/events/<writer>.jsonl
python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
```

The local-only events stay readable at `refs/fork-evidence/<date>`. Re-create
what they did with `ledger_cli.py append`; never copy their lines back. If the
next append on this machine is refused with StaleLogError, follow 8.1.

### 8.3 Voiding a bad record

You see an event that is wrong: a task created by mistake, a completion that did
not happen, a line the write gate would have refused that reached the remote
anyway.

It is never edited or deleted. A person other than its writer cancels it with a
void record, which is itself an event and says why. Any declared person may
void someone else's event; nobody may void their own.

```bash
grep -n '"seq":<n>,' "$SPACE/.datacore/events/<writer>.jsonl" | head -1
python3 "$LIB/ledger_cli.py" void --space "$SPACE" --log <writer>.jsonl --seq <n> --reason '<why it is wrong>'
python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
```

verify then reports the event as voided (`(1 voided record(s))`). If `void`
refuses, the refusal says why (your own event, not a declared person, the event
changed): that is the answer, not an obstacle.

### 8.4 Lost or rotated key

You see `~/.datacore/keys/<writer>.key` gone (a rebuilt machine, a wiped home),
or you think a key was exposed.

While signing is off, nothing stops: appends continue and verify does not ask
for signatures. Do nothing now, and do not copy a key from another machine.

If signing is on, a new key is registered only with the installation owner's
explicit approval, given from a machine other than the writer's. The approval is
itself a `key.rotate` event in the ledger; old events keep verifying with the
old key, new ones with the new key. On the owner's machine:

```bash
python3 "$LIB/ledger_keys_collect.py" --rotate <writer> --hosts <the writer's machine> --owner-approves
python3 "$LIB/ledger_cli.py" verify --space "$SPACE"
```

Each other machine then adopts the recorded rotation, without a second event:

```bash
python3 "$LIB/ledger_keys_collect.py" --rotate <writer> --owner-approves
```

Without `--owner-approves` nothing is written. A writer can never approve its
own new key, and an old key restored from a backup after a rotation is refused.

### 8.5 The doctor says a package is missing

Every other command says `the ledger cannot load (...)` and points at the
doctor. Install what the doctor names with the Python it names:

```bash
python3 "$LIB/ledger_cli.py" doctor
```
