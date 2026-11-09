# Brief for component-verification agents (2026-09-23)

You verify ONE cluster of Datacore core code with Lean 4, fix the defects you
confirm, and report. Read this whole brief first. The owner asked for:
"Use lean to verify whole datacore … fix all the issues discovered." The
worked example to imitate is `LedgerSpec/Item.lean` plus
`lib/tests/test_ledger_fold_formal_findings.py` and `lib/ledger/fold.py`
(read them). The survey entry for your cluster is under `survey/`.

## Setup

- Lean 4 v4.34.0, core only (no Mathlib). `~/.elan/bin/lean`, `~/.elan/bin/lake`.
- Project: `~/Data/.datacore/specs/datacore-lean`.
- Write your model to `DatacoreSpec/<Cluster>.lean`, with namespace
  `DatacoreSpec.<Cluster>`. Check it with
  `cd ~/Data/.datacore/specs/datacore-lean && ~/.elan/bin/lake env lean DatacoreSpec/<Cluster>.lean`.
  **Do NOT run `lake build`.** Other agents work in the same project at the
  same time. The coordinator wires imports and builds at the end.
- No `sorry`, `admit`, `axiom`, `native_decide`, and no `decide` on huge
  terms. Abstract with oracles and parameters, as `Item.lean` does.

## Method, per candidate

1. **Read the code**, not only the survey. The survey is a lead, not a fact.
   About a third of leads turn out to be design (see finding 1 in
   `README.md`: what looked like a bug was an intended conflict route, and
   the real defect was narrower). Look for tests and callers that encode
   intent before calling anything a bug.
2. **Model it in Lean**, branch for branch with the Python. State the claim
   from the docstring or DIP as a theorem.
3. **Either prove it, or refute it** with a concrete counterexample theorem.
   A proof that holds only because the model is vacuous is worthless.
4. **Replay every counterexample against the real Python**: an in-memory
   script, or a pytest in a tmp dir. Only a replayed counterexample counts as
   CONFIRMED.
5. **Fix** confirmed defects when the intended behaviour is clear from the
   docstring, DIP, tests or callers, and the fix is local to your files:
   - write a failing pytest first (`lib/tests/test_<cluster>_formal.py`, or the
     module's own `tests/` dir), then fix, then show it passing;
   - update the Lean model to the fixed code and PROVE the property;
   - **mutation-check**: put the bug back into a scratch copy of the model and
     confirm the theorem stops proving (scratch dir below).
6. **Do NOT fix; report as `NEEDS-OWNER`** when the fix is a policy or design
   choice, breaks an existing test that pins current behaviour on purpose,
   changes on-disk formats or ledger state roots, needs files you do not own,
   or touches another host or remote. Say exactly what the choice is.

## Hard rules

- **Edit only the files your cluster owns** (listed in your prompt), plus new
  test files and your Lean and findings files. Everything else is read-only.
  Other agents own other files right now. If a fix needs a file you do not
  own, report it.
- Never touch `.datacore/lib/deck_render.py`, `.datacore/agents/create-module.md`,
  `.datacore/dips/`, `lib/module_publish_scrub.py` or `lib/ws_chat_probe.py`.
  They have the owner's uncommitted work.
- No git commits, pushes, branch changes, `git stash`, or `git checkout` of
  files. No network calls to real services. No writes under `~/Data/*/org`,
  `.datacore/state`, `events/` or any real data. Use tmp dirs for replays.
- Never read or print secret values. Credentials are referred to by id only.
- Dates: never type a weekday from memory. Use
  `python3 ~/Data/.datacore/lib/date_utils.py dow YYYY-MM-DD` or Python `datetime`.
- Run only targeted tests (`python3 -m pytest -q <files>` from
  `~/Data/.datacore/lib`, or the module's tests dir). The
  coordinator runs the full suite.
- Scratch space: `<session scratchpad>/<cluster>/`.
- Budget: if a candidate is turning into a large model (>400 lines of Lean),
  model the core property only and say what you left out. Finishing all
  candidates at medium depth beats one at full depth.

## Deliverables

1. `DatacoreSpec/<Cluster>.lean`, which must check cleanly.
2. `findings/<cluster>.md`. For each candidate: verdict, one of CONFIRMED+FIXED,
   CONFIRMED+NEEDS-OWNER, REFUTED (the claim holds, and is proved), or
   DOWNGRADED (the lead was wrong, with why). Also give the Lean theorem
   names, the replay evidence (the command and its output, briefly), the files
   changed, the tests added, and the mutation-check result.
3. The final message to the coordinator is the same table, compressed:
   - one line per candidate;
   - the list of files changed;
   - the targeted test command and its pass count;
   - any NEEDS-OWNER decisions, each phrased as a question.
