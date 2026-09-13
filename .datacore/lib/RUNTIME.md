# Core and OCR runtime profile

`requirements-runtime.in` declares the core library, feed reader, optional
cloud speech formatter, transcript reader and OCR service dependencies. `requirements-runtime.txt`
locks their transitive versions and artifact hashes. It is separate from the
test-tool environment in `requirements-audit.txt`.

Install into a new virtual environment without `--system-site-packages`:

```sh
python3 -m venv /path/to/new-runtime
/path/to/new-runtime/bin/python -m pip --isolated install \
  --index-url https://pypi.org/simple --require-hashes --only-binary=:all: \
  -r .datacore/lib/requirements-runtime.txt
/path/to/new-runtime/bin/python -m pip check
/path/to/new-runtime/bin/python .datacore/lib/runtime_smoke.py
```

Install Tesseract and Poppler through the host package manager before the
smoke check. Keep the previous environment available until the new environment
passes qualification, then configure services to use its exact interpreter.
Do not move a virtual environment after creation: its entry-point paths may
refer to its original location.

The smoke check uses disposable synthetic images and mocked speech delivery.
It starts and stops the actual OCR MCP server twice, exercises image extraction,
checks a custom MCP lifespan, and exercises the installed gTTS formatter with
its unsafe transport and Click editor/pager functions disabled. It makes no
provider speech request. This check does not establish OS isolation, validate
fleet coordination, or qualify other modules' separate dependency profiles.
It also verifies the transcript adapter against actual library result types
with synthetic provider delivery, and runs the declared metadata CLI's local
version command with user configuration disabled. No video provider is queried;
provider availability and credentials require separate authorized checks.

CI checks the locked profile on Linux with Python 3.10, 3.12 and 3.14. A host
still needs its own service, native-library and installed-module qualification.

## Evaluated dependency behavior

gTTS 2.5.4 restricts Click to versions below 8.2. Those versions are affected
by [CVE-2026-7246 / PYSEC-2026-2132](https://github.com/pypa/advisory-database/blob/main/vulns/click/PYSEC-2026-2132.yaml),
which concerns editor/pager shell execution. The fix is described in the
[Click 8.3.3 release](https://github.com/pallets/click/releases/tag/8.3.3).
The inspected Datacore speech and OCR paths do not call these functions;
the runtime smoke check also rejects their use in speech generation. This is
a bounded reachability assessment, not a waiver for unrelated Click consumers.
Re-evaluate it when adding a CLI path or changing these dependencies. Do not
force an incompatible Click version past gTTS's declared requirements.

gTTS's own `stream()` and `save()` disable certificate verification and write
directly to the destination. Datacore uses only its request-body formatter.
`speech_transport.py` owns explicit cloud consent, bounded verified HTTPS,
redirect refusal and atomic publication. `public_download.post` enforces a
socket deadline even if the peer trickles response bytes.

MCP 1.28.1 with pydantic-settings 2.15 reports an unresolved `lifespan` annotation
warning while defining its settings class. Explicit lifespan callbacks and the
OCR stdio lifecycle passed the smoke check. Environment-derived callable
lifespans are not part of this profile; the warning is recorded, not suppressed.

Regenerate the lock using the command in its header with uv 0.12.13, then scan
the resulting package set and run compatibility checks before adoption. A
successful dependency resolver or zero scanner matches alone does not qualify
an installation.

Keep `--no-strip-extras` when regenerating. Python 3.10's bundled pip 23.0.1
otherwise treats the transitive `pyjwt[crypto]` requirement separately from the
bare pinned package and fails the hashed installation. CI exercises a new
standard-library virtual environment, including its bundled installer.

## Installed MCP verification

Full-mode MCP uses `requirements-mcp.in` / `requirements-mcp.txt` for its
Python helpers. This minimal profile is constrained to the same versions as
the core runtime: YAML discovery and cryptographic ledger verification, plus
their transitive dependencies. The discovery-only test subset is insufficient
for `ledger_health.py`. Optional module executors require their own declared
dependencies and qualification.

Provision a new environment without seeded installer packages. A separately
qualified pip installation (22.3 or later) can manage it with `--python`:

```sh
python3 -m venv --without-pip /absolute/new-mcp-python
python3 -m pip --isolated --python /absolute/new-mcp-python/bin/python install \
  --index-url https://pypi.org/simple --require-hashes --only-binary=:all: \
  -r .datacore/lib/requirements-mcp.txt
python3 -m pip --isolated --python /absolute/new-mcp-python/bin/python check
/absolute/new-mcp-python/bin/python -I .datacore/lib/mcp_runtime_smoke.py
```

The smoke check uses fresh helper processes, canonical nested-space discovery,
actual signed synthetic events, busy-writer refusal, missing-key uncertainty,
tamper detection and recovery. It accesses no operator stores or providers.
CI exercises this separate profile on Python 3.10, 3.12 and 3.14. Bind the
qualified interpreter through `DATACORE_PYTHON` in the provider profile below.

The checked-in `.mcp.json.example` is a template: replace every absolute
placeholder with a qualified installed path. Each provider starts through
`mcp_stdio.py --profile /private/provider/profile.json --credentials
/private/provider/secrets.json`, using the selected Python interpreter with
`-I`. Startup does not source the shared environment file or invoke a package
installer. Install and verify each required server beforehand; remove unused
provider entries from the client configuration.

Each profile and credential file must be a regular, single-link file with
permissions `0600`, in a private `0700` directory. Provision the declared
private HOME and working directory before startup. Directory/file aliases,
malformed or duplicate JSON, extra credentials and loader/environment overrides
are refused. A missing or invalid profile produces a generic startup error,
without including configuration or credential contents. For example:

```json
{
  "version": 1,
  "command": ["/opt/datacore/providers/node/bin/node", "/opt/datacore/providers/datacore/dist/index.js"],
  "home": "/private/provider/home",
  "cwd": "/private/provider/work",
  "environment": {
    "DATACORE_PATH": "/absolute/authorized/data-root",
    "DATACORE_LIB": "/opt/datacore/current/.datacore/lib",
    "DATACORE_PYTHON": "/opt/datacore/python/bin/python"
  },
  "credential_names": [],
  "credential_sha256": "44136fa355b3678a1146ad16f7e8649e94fb4fc21fe77e8310c060f61caaff8a"
}
```

These paths are illustrative, not an installed release layout. An empty
credential list requires an empty `{}` credential file. For a provider needing
an API key, declare that key's environment name in `credential_names` and store
only that exact key in its private credential JSON. Non-secret provider options
go in `environment`; do not duplicate credential names there. Python provider
commands must include `-I`. The selected virtual-environment entry point is
preserved even when its interpreter is a symlink to a base Python binary.

The profile's `credential_sha256` must match
`mcp_stdio.credential_digest(credentials)`, which hashes canonical JSON (sorted
keys, ASCII escaping, compact separators, no NaN). The example contains the
digest of `{}`. Compute the digest when provisioning or rotating credentials;
keep it private with the profile. A partially updated profile/credential pair
refuses startup even when the old and new credentials use identical key names.
Stop the provider, provision and validate both files, then restart it. This
binding detects mixed configuration versions; it is not an authentication
boundary against a principal who can modify both private files.

Profiles are trusted operator/administrator configuration. The launcher passes
only the explicit environment and stdio descriptors to the installed command.
It does not restrict that program's subsequent network access or filesystem
access under its OS identity. Run mutually untrusted contexts under the
independent runtime service identities; private HOME/environment selection by
itself is not OS or credential isolation. The active installation must still
be reconciled and tested before this template is considered deployed.

Managed MCP clients select the installed library with absolute `DATACORE_LIB`
and its qualified interpreter with absolute `DATACORE_PYTHON`. An explicit
unavailable selection must not fall back to code in the mutable data checkout
or to a different interpreter. The read-only `ledger_health.py --root ROOT`
helper emits version 1 JSON with verified, broken and unverified space counts.
It uses canonical marker/legacy discovery, the selected root's public-key
registry, chain checks and retained sequence witnesses. Busy writers, missing
verification inputs and incomplete discovery remain unverified. No data or
diagnostic excerpts are returned. This is a bounded local observation, not a
cross-host atomic snapshot or an OS isolation boundary.

The `space_catalog.py --root ROOT` helper exposes the same canonical discovery
as version 1 JSON (`spaces`: relative `path`, stable `name`, `type`, `marked`).
Root spaces use `.`. Duplicate names, malformed markers, incomplete traversal
and unresolved space aliases fail with a nonzero status and a content-free
error. MCP full mode requires this helper and the declared Python environment;
standalone core mode remains Python-independent. Clients must not substitute
numeric-directory scans if discovery fails. Personal capture requires exactly
one personal space (or a canonical unmarked legacy personal space); team or
ambiguous destinations cannot become the default. This routing rule does not
replace OS permissions or credential isolation.

## Private state and preserved upgrades

Module code stays in `.datacore/modules`; private mutable state belongs in
`[canonical-space]/.datacore/module-data/[module-name]`. This root and its
namespace directories must be private to the runtime identity (0700, files
0600). A module's `dataPath` points to its `data` subdirectory. Existing
`data`, `state`, or `settings.local.yaml` in the code or historical scoped
module directory prevent registration until explicitly migrated. Registration
must not silently substitute an empty store or move data itself.

Stop all writers, take and verify a backup (including SQLite WAL state), and
run the selected installed interpreter and helper as the data-owning identity:

```sh
/path/to/runtime/bin/python -I /path/to/core/.datacore/lib/module_data_migrate.py \
  --root /path/to/data --space stable-space-name --module module-name \
  --source /path/to/legacy/module --quiesced
```

`--quiesced` asserts an operator prerequisite; it does not stop services or
prove that a database is consistent. The helper verifies and flushes a private
copy, then retains originals under `.datacore/module-data-backups`. Its staged
receipt keeps MCP registration unavailable during an interrupted migration.
Retry the same command after correcting the failure. Changed originals or an
existing unrelated destination require reconciliation; neither is overwritten.
Cross-filesystem retirement fails with both copies retained. Arrange a supported
same-filesystem retirement before proceeding; do not delete the original to
bypass an incomplete receipt. Recheck ownership after any service-identity
handoff, read preserved records through the real module, and retain the backup.
Rollback must include writes made after cutover, not restore a stale snapshot.

Installed Python modules use `module_context.resolve` with their code path and
explicit `DATACORE_ROOT`; optional `DATACORE_SPACE` is a canonical stable name.
Without it, exactly one verified personal destination is required. The helper
enforces private directories and completed, space-bound migration receipts.
Read-only resolution creates no store. Its bounded file reader rejects aliases,
nonregular files, changed reads and malformed UTF-8. This shared routing path
does not replace the runtime's OS and credential boundaries.

The workflow CLI stores diagnostic phase state in absolute `DATACORE_STATE`,
defaulting to the runtime user's private `~/.datacore/state`. A code-relative or
data-root-relative `workflow_state.yaml` at a different location blocks writes
until preserved migration. Under stopped writers, retain the original, copy to
the private state directory, validate the YAML, and retire the legacy file to
private backup before restart. Phase updates serialize and atomically publish;
malformed or unreadable state is never replaced with an empty mapping. The CLI
remains scaffolding: its tool/agent/output handlers do not dispatch real work.

Private diagnostic state uses the shared `file_utils.private_state_directory`
boundary. Its absolute root and children must be owned by the runtime identity
and mode 0700, with no symlink aliases, unrelated writable ancestors, or Git
repository ancestry. Neither the selected data root nor installed code is a
valid state destination. Existing unsafe directories fail without silently
changing their ownership or permissions; reconcile them with writers stopped.
Workflow state, hook diagnostics, Org recovery journals, ledger transport locks,
and default decision boards use this same validation. Preserve pending recovery
journals while reconciling an existing state directory; changing permissions or
location does not resolve an interrupted transaction. Transport retains its
existing per-space lock inode, refuses file aliases, and waits at most 120
seconds for another local holder. This is local coordination only.

`intent_review.py` combines the owner's accessible spaces and therefore writes
only to `DATACORE_STATE/intent-reviews/<installation-digest>/`. The digest binds
the canonical data-root path; different installations do not replace each
other's reports. `--out` is a Markdown filename within that directory, not an
export path. Existing shared reports are retained without alteration. Review
their readers separately before any deliberate removal or redistribution.
Report generation and durable publication share one lock, and a failed build
or pre-publication write preserves the previous complete report. Reports are
0600 and source text is rendered literally. Moving the data root creates a new
report namespace and retains the old one. This command does not authorize
cross-space reads: its OS identity must already be entitled to every input.
