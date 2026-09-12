# Pinned Hermes runtime source

This kit prepares `hermes-agent==0.19.0+datacore.1` from the immutable public
0.19.0 source archive. It includes security dependency updates, tool-provenance
preservation during compaction, and transactional, replay-bounded todo writes.
Unreplayable historical todo state stops recovery instead of silently restoring
an older plan. Existing transcripts are preserved.

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
The regression tests are in `tests/test_datacore_compression_provenance.py` in
the prepared source. Qualify the selected upstream tests, actual integration
plugins and synthetic state migration before changing an active service.

Private plugins must be supplied through their authorized distribution path
and checked offline. Do not publish installed private package inventories to a
public resolver. Keep the previous environment and a consistent state backup
available for recovery; a package rollback alone does not undo a schema upgrade.
