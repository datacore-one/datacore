# Pinned Hermes runtime source

This kit prepares `hermes-agent==0.19.0+datacore.1` from the immutable public
0.19.0 source archive. It includes security dependency updates, tool-provenance
preservation during compaction, and transactional, replay-bounded todo writes.
Unreplayable historical todo state stops recovery instead of silently restoring
an older plan. Existing transcripts are preserved.

The backport also protects HTTP retries and persisted scheduler state:

- Webhook delivery IDs are scoped to the profile and route. Failed direct
  delivery remains retryable; a concurrent pending retry receives 503.
- API idempotency belongs to one adapter, endpoint and session scope. Identical
  concurrent requests share one complete response; conflicting reuse of a key
  receives 409. Failed and partial outcomes are not retained as successes.
- Responses report unsuccessful execution truthfully. Configured persistent
  storage cannot silently fall back to memory, damaged rows remain available
  for repair, and shutdown drains accepted HTTP requests before closing storage.
- Cron settings preserve the scheduler's current completed-run counter. Every
  mutation and automatic repair requires the correct OS file lock; contention
  or malformed storage refuses mutation without overwriting the source.
- File publication stages cross-filesystem copies before renaming them, and
  syncs content and the parent directory on the qualified POSIX runtime.

Idempotency records remain bounded and process-local. Webhook asynchronous 202
responses acknowledge admission, not durable completion. These controls do not
establish distributed execution ownership or an OS security boundary.

HTTP object payloads permit at most 64 nested containers; literal text does not
count as nesting. Busy bind-mounted individual files cannot support atomic
replacement: writes now fail while preserving the old file. Mount the containing
directory or use a rename-capable storage path. A directory-sync failure after
rename reports failure while retaining the complete newly published file.

`manifest.json` records the source, patch, lockfile and requirements hashes.
The upstream source and its license remain in the prepared tree. This kit does
not contain private plugins, credentials, installed inventories or session data.

## Profiles

- `datacore-telegram`: the upstream `all` profile plus Telegram.
- `datacore-telegram-tts`: Telegram plus the ElevenLabs integration.
- The corresponding `-verification` exports add the build and test tools.

The full upstream optional-dependency graph also includes Discord voice, whose
PyNaCl constraint still selects an advisory-matched version. These deployment
profiles exclude that backend. Do not use `--all-extras` or treat this kit as a
security-qualified installation of every optional integration. Profile selection
must match enabled services; adding an integration requires dependency and
runtime verification.

The qualified build target is Linux x86_64 with Python 3.12. Runtime compatibility
also requires each installation's plugins, configuration, state migrations,
credentials and service boundary to be checked.

## Preparation and build

Fetch the public archive URL in `manifest.json` into a disposable directory.
Preparation verifies its hash before extraction, rejects archive links and
escapes, verifies all build inputs, and requires a new output directory:

```sh
python3 prepare.py /path/to/hermes_agent-0.19.0.tar.gz /path/to/new-source
```

Use a separate OS execution context with an empty credential scope for builds
and tests. Download the selected verification requirements with
`pip download --require-hashes --only-binary=:all: --no-deps`, then install them
into a fresh Python 3.12 virtual environment with `--no-index`, `--find-links`,
`--require-hashes` and `--no-deps`. Build with:

```sh
python -m build --wheel --no-isolation /path/to/new-source
```

Install the resulting wheel with `--no-index --no-deps` and run `pip check`.
The regression tests are in `tests/test_datacore_compression_provenance.py`,
`tests/test_datacore_http_adapters.py` and the expanded
`tests/test_atomic_replace_symlinks.py` in the prepared source.
Qualify the selected upstream tests, actual integration
plugins and synthetic state migration before changing an active service.

Private plugins must be supplied through their authorized distribution path
and checked offline. Do not publish installed private package inventories to a
public resolver. Keep the previous environment and a consistent state backup
available for recovery; a package rollback alone does not undo a schema upgrade.

## Installed environment verification

Install the runtime export into a separate base environment without build/test
tools. Keep that base administrator-owned and read-only to the executor. Build
at its final absolute prefix: copying a virtual environment to a different path
can leave console-script interpreters pointing at the build directory.

Run the repository verifier with the installed Python in an empty working
directory (the kit must be supplied from an administrator-controlled path):

```sh
/path/to/runtime/venv/bin/python -I -B /path/to/kit/verify_environment.py datacore-telegram
```

Use `datacore-telegram-tts` for the ElevenLabs profile. The verifier authenticates
the selected requirements export and rejects missing, additional, duplicate or
different-version distributions. Private plugin overlays need a separate
declared artifact, integrity check and integration test. This package inventory
check does not establish OS isolation, backend health or successful migration.
