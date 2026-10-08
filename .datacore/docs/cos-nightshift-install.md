# CoS + Nightshift on a Mac Mini, with local Cursor clients

Use a local Datacore MCP on each workstation. Each machine has its own clone of
the user's private spaces. The existing Git ledger transport moves tasks and
results; there is no shared remote MCP requirement. The Mini runs both CoS and
Nightshift, under distinct declared writer identities. Do not copy another
user's Data directory, credentials, PLUR memory, persona or fleet schedules.

This guide covers the module/server layer. The desktop app and its full
personalized briefing pipeline are a subsequent UI integration. The portable
CoS profile below runs the module's cadence triage, briefing and delegation
review. It does not install the existing operator-specific Linux automation,
mail/health accounts, voice delivery, autonomous merges or a chat gateway.

## 1. Install matching code and dependencies

Follow the root `INSTALL.md` for a fresh Datacore installation. Install the
`gtd`, `chief-of-staff`, and `nightshift` modules at compatible revisions on the
Mini. Keep core and module revisions consistent across the three machines.
Use separate private Git repositories for spaces; a folder merely gitignored
inside the core checkout cannot synchronize on its own.

The server profile uses the installation's `.datacore/venv/bin/python3`.
Install Nightshift's locked execution dependencies into that environment:

```sh
.datacore/venv/bin/python3 -m pip install --require-hashes -r .datacore/modules/nightshift/requirements-execution.txt
.datacore/venv/bin/python3 -m pip check
npm ci --prefix .datacore/modules
source .datacore/venv/bin/activate
```

The module dependency step matters: MCP discovery alone can succeed while GTD
tools fail to load. Install the Datacore MCP/CLI and PLUR MCP/CLI using the
qualified runtime profile in `INSTALL.md`, and authenticate the chosen executor
on the Mini as the service user. The current Nightshift execution profile needs
the Claude CLI even if the interactive workstation uses Cursor. No package is
downloaded at scheduled-job startup.

## 2. Initialize the ledger once, then clone it

Use distinct writer names, e.g. `work-mac`, `work-windows`, `cos`, `nightshift`.
Register the workstation writers as belonging to the human principal; register
the services as agents. Distribute the installation's private principal/host
configuration to its own machines, not to a public module repository. Keep
private signing keys local to their writer.

For each new private space, on the first workstation:

```sh
python3 .datacore/lib/ledger_cli.py init --space 0-personal --actor work-mac
python3 .datacore/lib/ledger_ingest_org.py --root .
python3 .datacore/lib/ledger_phase1_flip.py --root . --space 0-personal --flip
python3 .datacore/lib/ledger_phase1_flip.py --root . --space 0-personal --flip --apply
```

`init` supplies the edit protocol and local-state ignore rule. The flip checks
preservation before making `next_actions.org` a generated view. Existing
installations must upgrade every active reader before enabling a new edit
protocol; never repair a mismatch by overwriting a projection baseline.
Commit/publish the space's initialized files, then clone that space on the
other machines. Give each machine its own local identity; do not clone the
first machine's private identity or runtime-state directory.

Reports must be tracked in the private space. If its ignore rules exclude the
drop folder, explicitly allow the intended report files, for example:

```gitignore
!0-inbox/
0-inbox/*
!0-inbox/nightshift-*.md
```

Review existing rules rather than pasting over them. `server_setup doctor`
checks a representative Nightshift report path. It does not upload artifacts.

Signing is optional and must be described honestly. With signing off, chain
integrity is checked but a cryptographic author claim is not established. With
signing on, enable `DATACORE_LEDGER_SIGN=1` for each writer and distribute only
the public registry through the trusted installation configuration. The server
setup's `--sign` creates the two service keys using the existing key manager;
it never creates the human's private key. Confirm public keys before trusting
them. Check all replicas with:

```sh
python3 .datacore/lib/ledger_cli.py verify --space 0-personal --full
```

Use `--strict` for a space whose complete history is signed. Unknown/missing
verification keys are not a pass. Enabling signing does not retroactively sign
old events. Separate actor names are not OS isolation; service accounts and
credential access determine the actual privilege boundary.

## 3. Connect Cursor locally

On macOS, run:

```sh
python3 .datacore/adapters/cursor/install.py --root .
python3 .datacore/adapters/cursor/install.py doctor --root .
```

Open the workspace and approve its MCP servers in Cursor. The installer never
forges that approval. Test command discovery and a structured task read.

On Windows, use WSL2 for the Datacore runtime and Linux filesystem for its clone.
Native Windows task storage is not supported by the POSIX locking code. See
[Microsoft's WSL setup guide](https://learn.microsoft.com/windows/wsl/setup/environment).
Install the same Python/Node dependencies inside the distribution. If Cursor
runs its workspace tools inside WSL, use the ordinary installer there. For a
Windows-side Cursor process accessing that same WSL workspace, run inside WSL:

```sh
python3 .datacore/adapters/cursor/install.py --root . --wsl-distribution Ubuntu
```

Replace `Ubuntu` with the installed distribution. This generates `wsl.exe`
argument arrays for local MCP and guard hooks, with Linux paths and environment
variables passed inside WSL. It does not create another copy of the tasks or
route them through the Mini. Approve the servers in the actual Cursor window.
Verify a task write there; generated configuration tests do not prove the
Windows/WSL/GUI combination. Never work alternately on independent Windows and
WSL copies without the normal Git synchronization.

`/wrap-up` can use `gtd.add_task` with `body` and `properties`. Explicitly
delegated tasks include `SURFACE`, `DONE_WHEN`, and `ROADMAP` where required.
Ordinary task capture does not imply authorization to run. The result returns
the task ID and local ledger writer; successful sync is a separate receipt.
Use repository-relative paths in instructions another machine will execute.

Run the existing phase-1 cycle after capture and when opening the workspace:

```sh
bash .datacore/lib/ledger_phase1_cycle.sh
```

Local capture works offline. It is pending handoff until transport succeeds;
do not report it as delivered merely because the MCP returned a task ID.

## 4. Prepare and activate the Mini

Run as the intended service user from the installed Data root. Set the root
explicitly for commands outside that directory. The following is a generic
profile; `owner` is the user's registered human principal, not a copied name:

```sh
.datacore/venv/bin/python3 .datacore/lib/server_setup.py plan --root . --owner owner --host mini
.datacore/venv/bin/python3 .datacore/lib/server_setup.py doctor --root .
.datacore/venv/bin/python3 .datacore/lib/server_setup.py apply --root . --owner owner --host mini
```

Add `--sign` to both plan and apply if signing was selected. Apply checks
prerequisites, declares the service writers, and writes a local server profile
and `schedules.local.yaml`; it starts nothing. A rerun refuses to overwrite a
different local profile. If a roster exists, register both service writers on
the selected host first. Adapt permissions, policy and persona for this user.

The minimal profile contains only three jobs, using the Mini's local timezone:

| Job | Schedule | Writer |
| --- | --- | --- |
| Ledger convergence and projection | Every 15 minutes | CoS |
| Nightshift execution | 02:00 | Nightshift |
| CoS cadence briefing and review | 07:00 | CoS |

CoS briefings land in the selected space's `notes/briefings`; Nightshift reports
land in the task's space. No second morning briefing producer is installed.
Chat remains optional. A later chat adapter must use the existing approval
store and authenticated human decisions; a chat model may not approve itself.

After the acceptance drill below and executor authentication, activate:

```sh
.datacore/venv/bin/python3 .datacore/lib/server_setup.py activate --root .
.datacore/modules/nightshift/nightshift scheduler status
```

These are user LaunchAgents, so the service user must be logged in after boot.
Configure the Mini's sleep/login behavior for the desired availability and test
reboot and wake. Do not describe a logged-out LaunchAgent as a boot-time daemon.
Logs are in `~/.datacore/logs`. Use the generated profile rather than the
operator-specific Linux `cos-server-setup.sh` or the fleet's general schedules.

## 5. Audit and acceptance

Nightshift records task identity, claim, clocks, outcome, output path/hash and
delivered code commit/PR evidence. `publication` distinguishes `published`,
`pending`, `local-only` and `unknown`. A completion event is the worker's
statement, not proof of correctness: phase-1 work stays REVIEW until another
actor verifies it. Inspect actual artifacts before accepting work.

Failed lifecycle appends retain private retry intents outside Git. The next run
reconciles them before admitting more work in the affected space; other spaces
can continue. Stable audit IDs make a retry after a lost acknowledgement
idempotent. `observed_at` preserves the original observation time. If even the
recovery write fails, the run reports incomplete evidence and retains the
produced output; no system can promise durable records on unwritable storage.

```sh
.datacore/modules/nightshift/nightshift status
.datacore/modules/nightshift/nightshift audit-reconcile
python3 .datacore/lib/ledger_done_report.py report --root . --days 7
```

Recovery never reruns tasks. Recovered ledger records still need normal Git
publication. Signed events and a second logical actor do not prove independent
review if both writers share credentials or filesystem privileges.

Before handing over the installation, perform one harmless delegated task
from each actual Cursor workstation:

1. Capture through `/wrap-up`; inspect ID, writer, body and completion criteria.
2. Capture while offline; confirm it says locally saved, then reconnect/sync.
3. Confirm the Mini receives the same ID and claims it once.
4. Execute with the authenticated provider and inspect the report/commit.
5. Confirm the report reaches both workstations and remains REVIEW.
6. Record the human's verification and confirm both projections agree.
7. Restart the Mini and repeat sync; confirm no duplicate execution or records.
8. Check CoS's briefing output and selected chat delivery, if configured.

The automated replica drill is `.datacore/lib/tests/test_local_mcp_server_handoff.py`.
It uses the actual MCP handler, adapter, transport, Nightshift bookkeeping and
ledger fold with a deterministic output. Separate fault tests cover partial
appends and lost acknowledgements. Neither test replaces the actual Cursor,
Windows, launchd or authenticated-provider checks above.
