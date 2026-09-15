# Projection and archive guarantees

`project(state, space=..., as_of=...)` renders a view of folded ledger state.
`as_of` is an optional finite UTC epoch timestamp in seconds. With it, verified
and dismissed tasks remain visible for the one-day retention window. Agent
completions remain in REVIEW until human verification. Housekeeping closures
remain excluded. The same state and retention instant produce the same view
independently of the host clock and timezone; CLOSED timestamps use UTC.

Without `as_of`, replay and checkpoint callers retain terminal records rather
than expiring them according to the machine's clock. This changes the previous
implicit rolling-window API. `ledger_project_org.py` and generated-edit
ingestion explicitly select the current time for their action-list view.
Callers comparing rolling views must pass the same instant to both renders.
The event ledger retains history regardless of projection retention.

Only selected items contribute file tags. A combined view promotes tags shared
by every selected item to its header and preserves other source-file tags on
their own headings. Cyclic parent links are refused rather than silently
omitting their tasks; deep valid hierarchies use an iterative traversal.

`ledger_done_report.py archive` writes verified and dismissed tasks to monthly
UTC archive files using the canonical task renderer. It preserves bodies,
properties, priority, planning timestamps and inherited tags. Agent completion
alone is not archival authorization. The report operation may still include
agent completions as work awaiting review.

Archive writes use the recoverable local Org transaction: all monthly targets
are checked before mutation, concurrent local invocations serialize, and a
failed write or durability barrier rolls back before a retry. ID-shaped text
inside notes is not a task identity. An existing matching identity is skipped
only when its complete rendered content agrees. A conflicting or incomplete
legacy entry is preserved and reported for reconciliation; the tool does not
overwrite authored archive corrections or treat a lossy legacy entry as a
successful complete archive. Symbolic links and multiply linked files are
refused.

These guarantees do not provide distributed exclusion or a security boundary
against another process with the same filesystem privileges. Reconcile legacy
archive conflicts and establish authentic projection baselines before enabling
unattended writes in an older installation. Never manufacture a baseline from
whichever conflicting copy happens to be available first.
