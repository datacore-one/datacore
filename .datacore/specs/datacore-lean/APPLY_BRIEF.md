# Brief: applying the owner's decisions (2026-09-23)

The owner reviewed `DECISIONS.md` on a decision board and said "apply the
decisions". Your prompt lists the decisions assigned to you, each with its id
(e.g. L7) and the chosen option. **These are decided. Implement them as stated;
do not re-open them.** If one turns out impossible or unsafe as stated, stop on
that item and report why. Do not substitute your own policy.

Everything in `AGENT_BRIEF.md` still applies (read it): file ownership, no
commits, no network, no real data, secret handling, dates, targeted tests
only, `lake env lean` and never `lake build`, scratch dir. In addition:

## Per decision

1. **Failing test first.** Add it to `lib/tests/test_decisions_<area>.py`, or to
   the module's tests dir. It must fail on the current code.
2. **Implement** the smallest change that satisfies the decision.
3. **Update existing tests** that pin the old behaviour, but only where the
   decision explicitly changes that behaviour. Name each changed test in your
   report.
4. **Update the Lean model** under `DatacoreSpec/` (or `LedgerSpec/`) when it
   covers the changed code. The theorems must describe the new behaviour.
   Mutation-check any theorem that changed: put the old behaviour back in a
   scratch copy and confirm the theorem breaks.
5. **Update the docs the change touches:** docstrings, the module's CLAUDE.md
   or README, and the matching `findings/*.md` entry (append
   "Decision <id> applied: …").
6. **Out of bounds — report only, never do:** anything that reaches another
   host, a remote, PyPI, the `.datacore/dips` repo, or real data under
   `~/Data/*/org`, `events/` or `.datacore/state`. Migrations of real files are
   delivered as a script plus a dry-run report against a COPY in scratch; they
   are never run on real data.

## Report to the coordinator

- One line per decision, with its status: APPLIED, APPLIED-PARTIAL (what is
  left), or BLOCKED (why).
- The files changed, and the tests changed or added.
- The targeted test command and its pass count.
- The Lean file(s) re-checked, with their theorem changes.
- Any follow-up that needs the owner, phrased as a question.
