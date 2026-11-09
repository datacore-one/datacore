# Decision-board application and recovery

This implementation contract follows implemented DIP-0009's current task/file
vocabulary and DIP-0046's source-of-truth rules. It does not introduce a new
execution or distributed ownership guarantee. The presentation skill is
`.datacore/skills/decision-board/SKILL.md`.

A board is a private local review artifact. Its schema-2 build hash binds its
rows, offered choices, resolved input paths, source bytes and persistence model.
Generated inputs must agree with their available ledger before review. The hash
is a content identity, not a signature or an independent OS authorization
boundary. The owner must explicitly authorize applying the selected decisions.

Saved choices require the matching board slug and build. Unknown rows/options,
duplicate keys/identities, changed inputs, stale ledger state and changed
persistence modes refuse before mutation. A rebuilt positional row cannot inherit
an older choice accidentally. Already applied rows cannot be revised from an old
board; the current task must be reviewed again.

Application uses the canonical Org adapter in the current process. One row's
move/update and completion receipt share its recoverable Org transaction. Before
that transaction, a pending intent is durably published. Successful rows have
independent durable receipts, so a later row's failure never hides earlier
success. A restart recovers an interrupted Org transaction before inspecting
receipts. Pending intents block blind retries.

The ledger is append-only and its events cannot be rolled back with an Org undo
journal. A generated decision plans against the reviewed ledger root and emits
conditional edits using the existing edit protocol. Concurrent changes to the
same fields conflict; disjoint fields retain the protocol's three-way merge
semantics. Terminal dismissal checks the complete task payload. An observed
conditional rejection is reported as failure, with the event retained for
reconciliation. No claim of exclusive cross-host execution is made here.

If a row is pending, preserve the board, decisions, receipt and any transaction
journal. Run the normal recovery path, inspect the current source and folded
ledger, and reconcile retained conditional conflicts or partial ledger effects.
Do not remove pending receipts or retry their operations blindly. Once source
and ledger agree, create a fresh board to obtain a new decision on that state.
The prior receipt remains recovery evidence. This conservative stop is necessary
when the system cannot prove whether an append-only side effect completed.

DIP-0009 distinguishes passive someday tasks from considered, benched work.
Authored tasks move to `someday.org` as TODO with full content preserved. Generated
next-actions remain in place as DEFERRED and receive the displayed future wake
date; they are not dismissed. The board describes these different consequences
before the owner chooses. Planning delegation writes a request for review and
never implies that a worker was started. Choosing NEXT changes state in the
current file; it does not claim to have processed or refiled an inbox capture.

Artifacts default to the private runtime state directory. Publication uses
owner-only files and atomic replacement; symbolic/hardlinked artifacts and
public output directories refuse. Scripts and styles are inline, no external
fonts are requested, and the browser policy blocks network subresources.
Reference links are explicit HTTP(S) only, without referrer/opener authority.

## Morning review board

`.datacore/lib/review_board.py` (job `mac-review-board`, first arrival of the
day on the Mac) builds one board of every REVIEW task across the spaces through
`gtd_decision_board.build`, so build identity, receipts and `apply` are the ones
above. Rows offer Accept / Rework it / Drop / Already done and carry evidence in
the meta line (report path, test result, second-model review) and as HTTP(S)
links (PR, issue). Owner rules live in `.datacore/config/review_rules.local.yaml`
(gitignored; the shipped `.example` passes nothing). A listed review type passes
only with green tests and a passing reviewer from a different model family; such
rows sit in their own section, pre-set to Accept, and the same choices are saved
as an answers file that `apply` accepts. Nothing is applied by the job. Release,
security, money, credentials and public posts never pass without the owner: the
rules cannot name them, and a task whose type, tags or title touches one stays
with the owner.
