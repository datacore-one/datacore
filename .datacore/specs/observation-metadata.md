# Observation metadata and learning review

The observation hook records operational metadata: event, a known tool category,
success/failure, timestamp, and SHA-256 session/workspace identifiers. Unknown
tool names use the `External` category. Identifiers support local grouping; they
are pseudonymous, not a claim of anonymity or an authorization boundary.
Tool inputs, nested values, errors, raw session identifiers, and filesystem
paths must not enter observation records or hook output.

Files under `PLUR_PATH/observations` (default `~/.plur/observations`) are private
to their owner. New records use `YYYY-MM-DD.metadata.jsonl`, schema version 2,
with explicit size and time-window limits. A writer retains an interrupted tail
and starts the next record on a new line; partial metadata cannot consume the
next acknowledged record. The hook is best effort: refusal or write failure
emits a content-free warning and an empty hook response.

Legacy raw `YYYY-MM-DD.jsonl` files are preserved. The writer restricts its owned
observation directory to mode 0700. Automatic analyzers read only validated
version-2 metadata. Historical contents require explicit local review before
any reuse, migration, or deletion; they must not be silently imported into
learning. Existing deployments must reconcile old observation commands with
the repository's `hooks/plur_observe.py` producer.

DIP-0019 is Implemented. Sections 6 and 7 require human review of concrete
learning candidates and cross-domain abstraction, with deferral as the default.
The historical `plur_auto_promote.py` command therefore generates proposals
for `/daily-review` or `/learn`; observation counts alone do not publish a
global engram. Its `--dry-run` flag remains compatible. The analyzer's historical
`--auto-learn` option fails with a review instruction rather than implying that
an engram was created. No change to the DIP's normative requirement is made.
