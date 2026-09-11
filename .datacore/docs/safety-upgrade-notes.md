# Safety upgrade and compatibility notes

This change adds preservation transactions, stricter authenticated interfaces,
and versioned ledger settlement. Back up local source files, configuration and
ledger logs before rolling it out. No migration deletes original content.

## Python environments

Install `.datacore/lib/requirements.txt` in the product's dedicated environment.
Cryptography now requires 50.0.0 or newer. The OCR profile requires Pillow
12.3.0 and MCP 1.28.1 or newer; the research profile also declares that MCP
security floor. Existing environment pins may conflict;
resolve them in a separate environment before switching services. Do not assume
that editing a requirements file upgrades an already running service.

`.datacore/lib/requirements-audit.txt` pins the direct and transitive dependencies
used by the security CI regressions. Full installations also have optional module
and native dependencies, which need their own deployment verification.

## Ledger settlement and execution

Upgrade all readers before emitting version-2 seals. Version 2 identifies each
independent log, binds its exact event set, and hashes all folded item fields.
Legacy seals remain in the append-only history but require a fresh seal before
settlement can be verified. Run the sequencer's existing `ledger_seal.py emit`
command for each space, then `ledger_seal.py status`. Do not edit old events.

Claims now bind the selected payload hash. Tasks claimed by older writers need
explicit review before execution; a missing binding must not be invented from
current state. Approval grants also bind the entire proposed task payload.

Foreground subprocess timeouts now stop their inherited POSIX process group.
Scheduled jobs default to a one-hour timeout; a job can declare
`timeout_seconds` up to 86400 for longer work. The runner requires the shared
Datacore library beside its source or at its configured root. Deliberately detached descendants still
require an independently enforced OS process boundary.

Local file locks serialize cooperating processes on one host. Git replication
and independently writable logs do not provide globally exclusive execution.
Tool-call classifiers are runtime guards; they do not isolate arbitrary code
running under the same OS identity. Deployments accepting untrusted tasks need
an independently enforced execution sandbox and isolated credentials. These
properties require deployment verification before claiming cross-host or
untrusted-runtime enforcement.

OpenClaw dispatch requires an installed version supporting `agent exec` with
`--message-file`, `--cwd` and JSON output. Hermes dispatch requires the guarded
wrapper and its matching Python environment. Unsupported runtimes fail instead
of falling back to a less restricted invocation.

## Persistence and recovery

Org mutations, archive moves and paired credential additions use a private undo
journal under `DATACORE_STATE` (default `~/.datacore/state`). An interrupted
operation is recovered by the next cooperating command. If a file changed
outside its transaction, recovery stops and retains the journal for review.
Preserve both the journal and current files; do not delete the journal to hide
an unresolved conflict.

Queued writebacks bind the original source and complete intended result before
publication. Legacy unprepared queue rows remain conflicts until reviewed.
Archive collisions preserve both notes. Duplicate task retirement retains the
original identifier and content. OAuth token replacement preserves the exact
outgoing credential bytes in a private backup history.

Git conflict recovery no longer chooses one side of unfamiliar files or combines
conflicting Org edits automatically. Ambiguous files and their index stages remain
for review. The ledger resolver can stage a validated prefix extension but never
commits the entire index. Cadence recovery requires both intact stages and an
unchanged conflict file. Relays stop on merge/integrity failure and retain local
history; they do not rewind with `reset --hard`.

Publishing knowledge from another branch refuses to replace a destination that
changed independently. It retains working copies; reconcile an untracked file
explicitly if it prevents a later checkout. Research publishes only recorded,
unchanged outputs from an initially clean checkout. Existing edits, changed
outputs or publication failures leave the files local and report failure. Review
and publish those retained files explicitly after resolving the reported conflict.
Wrap-up dry runs are read-only, and concurrent journal appends share the preserving
transaction. The ledger publisher includes new writer logs and retries committed
work after a failed push even if no new files changed. It refuses outgoing history
that would also expose unreviewed human files, including added-then-reverted files.

Unchanged cross-branch publication still retries the requested push. Worktree
cleanup requires a stopped worker and refuses tracked, untracked or ignored
uncommitted files and uncertain Git history. It never forces removal or discards
unmerged branches; preserve or explicitly reconcile unfinished work first.

Atomic archive moves require native no-replace rename support and the same
filesystem. Cross-device moves fail with their sources preserved; they do not
fall back to a copy-then-delete sequence.

## HTTP and background recovery

Configure nonempty credentials before starting relay, webhook or daemon health
services. Authenticate requests with their documented headers; query-string
credentials are refused. Default binds are loopback. Health responses do not
include credential or private filesystem metadata.

Authenticated direct API clients require HTTPS, reject redirects, disable ambient
proxies and bound response reads. Deployments relying on an implicit proxy or
redirect need an explicit reviewed configuration instead.

Public URL downloads enforce their deadline across DNS resolution, connection,
headers and body. DNS runs in a disposable Python process; these downloads
require a POSIX runtime and permission to start that helper process.

Agent relay outboxes are separate from synchronized stream files. Install and
configure the supplied outbox service/timer for retry while no new events arrive.
Standalone stream tailers require the shared Datacore library and a private state
directory. The daemon health and dispatcher services are user units and the dispatcher requires its private
`~/.config/datacore/dispatcher.env` file with the machine's unique actor identity.

Hook state now lives in a private `hook-state` directory under `DATACORE_STATE`.
Shared `/tmp` flags are not trusted or imported. Restart a demo session with an
explicit demo prompt to establish its new private sticky flag.

Command receipts keep existing UTC log partitions and select a requested local
calendar day using each timestamp. New receipts omit raw arguments; existing
history is preserved. A receipt proves invocation only. Query errors return a
failure status, and a missing receipt is not proof that no command ran.

The WAHA compose template binds loopback, pins an image digest, requires an API
key and disables optional dashboards, Swagger and QR logging. Verify the actual
container and network configuration before deploying the template.
