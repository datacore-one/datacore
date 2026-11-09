# Findings: Dates cluster (2026-09-23)

Model: `DatacoreSpec/Dates.lean` (namespace `DatacoreSpec.Dates`). It checks with
`lake env lean DatacoreSpec/Dates.lean` and has no sorry, admit, axiom or native_decide.
Axioms used: `propext`, `Classical.choice`, `Quot.sound`. `table_ok` uses none.
Tests: `lib/tests/test_dates_formal.py` (15 tests). All 15 failed or errored before the
fix, except 4 that pin behaviour that must not change. All 15 pass after it.
Replay script: `scratchpad/dates/replay.py`, run against the real modules.

| # | Candidate | Verdict |
|---|---|---|
| 1 | Weekday model | PROVED (no defect) |
| 2 | Hook rewrite relation | Lookahead fix PROVED; `\s` line-join CONFIRMED+NEEDS-OWNER (coordinator's file) |
| 3a | `date_utils.fix_day_names` / `find_mismatches` | CONFIRMED+FIXED |
| 3b | `parse_relative("next month")` | CONFIRMED+FIXED |
| 4 | `org_date_validator` hard-coded 2025, and the same prefix bug | CONFIRMED+FIXED (window width asked of the owner below) |
| 5 | `validate_org_dates` frontmatter regexes applied to the whole file | CONFIRMED+FIXED |
| 6 | `decay_and_review.month_keys` | CONFIRMED+FIXED |

## 1. Weekday (days-from-civil)
- `table_ok`: the model agrees with Python `date.weekday()` on 16 dates. They run from 0001-01-01 to
  9999-12-31 and include the 1900, 2000 and 2100 century cases and 2024-02-29.
- `epoch`, `next_day`, `month_step` (every month steps by `daysIn`, including the Gregorian leap
  rule). With these three, the model is the calendar for every date, not only for the table.
- `weekday_range`, `weekday_shift` (7-periodicity), `cycle400` / `weekday_cycle400`.
- Mutation: removing `- yoe/100` breaks `table_ok` and `month_step` (5 errors).

## 2. Rewrite relation of the day fix (`re.sub` over DATE_PATTERN)
The model is `Pat` (blank class and lookahead flag), `matchAt`, `repl` and `scan`/`fix`. It follows
`re.sub` left to right with no overlapping matches. The date-to-day answer is an oracle, and the
theorems hold for every oracle that answers with a day abbreviation (`OracleOk`).
- `fix_rw` (with the lookahead): the output is the input with only whole stamp tokens replaced. A
  stamp token is a date, blanks, and a day abbreviation that is not followed by a letter.
- `fix_idem`: a second pass is a no-op. This holds for any blank class that excludes digits, `-` and
  letters, with or without the lookahead. The key lemma is `window`: a match that starts before a
  date ends before that date. So a rewrite never creates a new match.
- Refuted (replayed): `old_monitor` gives "2026-09-24 Monitor" → "Thuitor", and `old_monday` gives
  → "Thuday". `old_not_a_token` shows the old pattern matched "Mon" when "itor" followed it.
- **New, CONFIRMED in the coordinator's hook** (`hook_joins_lines`): `\s+` crosses a newline.
  `org_date_hook.fix_dates` on "Due 2026-09-24\nSat with Bob" writes "Due 2026-09-24 Thu with Bob".
  The two lines are joined and the word "Sat" is lost.
  Replay: `F hook newline: 1 'Due 2026-09-24 Thu with Bob\n'`.
  `hook_fix_sound` still holds: idempotence holds, and the rewrite changes only tokens. The token is
  a real one under `\s`. The fix is to use `[ \t]+`, as `date_utils` now does. Proved for that
  variant: `new_fix_sound` (only tokens change, the newline count is preserved, and the fix is
  idempotent), and `new_keeps_lines`.
- Mutations: see the table at the end.

## 3a. `date_utils.DATE_DOW_RE`: CONFIRMED+FIXED
The bug was the same as the hook's. It also caused a second problem: `find_mismatches` drives the
PreToolUse hook `hooks/org_date_prewrite.py`, which **blocked** any Edit/Write containing
"2026-09-24 Monitor". Replay before the fix: `fix_day_names` → "Thuitor", and `find_mismatches`
flagged it. After the fix: unchanged, `[]`, and the prewrite hook exits 0.
The fix is `[ \t]+(Mon|…|Sun)(?![A-Za-z])`.

## 3b. `parse_relative("next month")`: CONFIRMED+FIXED
`re.match(r"(next|last)\s+(mon|…)")` is a prefix match, so "month" matched as "mon". "next month"
from 2026-09-23 gave 2026-09-28 (a Monday). The docstring promises `next/last {…|week|month}`.
- Fix: `fullmatch` on the word. `week` means ±7 days (unchanged). `month` uses the new
  `add_months(d, ±1)`: the same day in the adjacent calendar month, clamped to that month's last
  day. A day word must be at least 3 letters and a prefix of a full day name, so "mon", "tues" and
  "thursday" are accepted. The docstring now says this.
- Lean: `old_month_is_monday` (refuted), `new_month_rejected`, `new_accepts_days`, `dayNew_sound`,
  `addMonths_spec` (the month index moves by exactly n, the result is a valid day, and it is ≤ the
  original day), `addMonths_examples`.
- Behaviour change: trailing text is now rejected. "next monday please" used to parse. The only
  caller is the CLI `parse` subcommand.

## 4. `org_date_validator`: CONFIRMED+FIXED (plus the same prefix bug)
- It used the literal `2025` as the suspect year. On 2027-01-01 the check would silently stop
  flagging last-year dates. Now `suspect_year(today) = today.year - 1`, and `validate_file` takes
  a `today=` argument for tests. This reproduces the meaning the code had when it was written.
  Lean: `suspect_new_is_last_year`, `suspect_old_stale`.
- Its `DATE_PATTERN` had the same Monitor/Monday prefix bug, and its `fix` mode rewrote files. The
  fix is the same pattern. Replay before the fix: `SCHEDULED: <2026-09-24 Thuitor>`.

## 5. `validate_org_dates` frontmatter: CONFIRMED+FIXED
`FM_DATE`/`FM_DAY` used `re.M` over the whole file. A body line `date: …` was paired with any later
`day: …` line, for example one inside a YAML code fence. Replay: this caused a false error in the
**pre-commit gate**, and `--fix` rewrote the body. `\s*$` also ate the trailing newline in `--fix`.
The fix is `frontmatter_span`: the search now runs only inside a leading `---` … `---`/`...` block,
with `[ \t]*` in place of `\s*`. Lean: `fmNew_frame` (the verdict is independent of the body),
`fmNew_no_frontmatter`, and `old_reads_body` (a refutation, and the frontmatter case still fires).

## 6. `decay_and_review.month_keys`: CONFIRMED+FIXED
`now - 30*i days` from Mar 31 gives Mar 31, Mar 1 and Jan 30, so February's history is never read.
Replay: `old_keys_skip_february`, and the Python replay matches. The fix uses a month index:
`divmod(idx - i, 12)`, with an optional `now=` argument. Lean: `month_keys_distinct` (N keys,
pairwise distinct, each a valid month, key i exactly i months back) and `new_keys_march31`.

## Mutation checks (scratch copies, `scratchpad/dates/mut.py`)
Every mutation stops the named theorem from proving:

| Mutation (bug put back) | Breaks |
|---|---|
| drop `- yoe/100` (no century rule) | `table_ok`, `month_step` (5 errors) |
| matchAt ignores the lookahead | `matchAt_some` → `fix_rw` (3 errors) |
| `newPat` blanks include `\n` | `new_fix_sound` via `Rw.newlines` (4 errors) |
| month key steps by `i/2` | `month_keys_distinct` (3 errors) |
| day word by 3-letter prefix of the input (old rule) | `new_month_rejected`, `dayNew_sound` (2 errors) |
| frontmatter read from the whole file | `fmNew_frame`, `old_reads_body` (2 errors) |
| blank class may contain `-` | `window` → `fix_idem` (1 error) |

## Files changed
- `.datacore/lib/date_utils.py` (DATE_DOW_RE, `_day_word`, `add_months`, parse_relative)
- `.datacore/lib/org_date_validator.py` (pattern, `suspect_year`, `today=`)
- `.datacore/lib/validate_org_dates.py` (`frontmatter_span`, FM regexes)
- `.datacore/lib/decay_and_review.py` (`month_keys` only)
- New: `.datacore/lib/tests/test_dates_formal.py`, `DatacoreSpec/Dates.lean`, this file.

## Not covered / residue
- `\d` and `\s` are modelled on ASCII only. Python's are Unicode.
- The lookahead `(?![A-Za-z])` is ASCII-only. A day abbreviation followed by a non-ASCII letter
  (the "Mon" + "a-umlaut" case) still counts as a stamp, and the prewrite hook blocked this very
  file for that reason. The fix would be `(?![^\W\d_])` in all three patterns, the hook included.
- `strftime('%a')` depends on the locale. The model assumes the C locale, which is Python's default.
- The `in N days` / `N days ago` regexes still use prefix `re.match`.

**Decision D10 recorded (2026-09-23): keep.** The "suspect year" stays at last year
only. No code.

## Decision G8 applied (2026-09-23): the hook writes under the org lock

`org_date_hook.fix_dates` no longer calls `write_text`. It computes the fix
on an unlocked read (a file with nothing to fix takes no lock), then re-reads
and writes inside `org_transaction.serialized` (`watch_file`,
`write_org_text`: atomic and journalled). It waits for the lock at most
`LOCK_TIMEOUT = 2.0` s; on a busy lock, a retained journal or any other
transaction error it skips the fix, prints one stderr line, and exits 0. The
rewrite itself (`_fix_text`, DATE_PATTERN) is unchanged, so sections 2 and 3
still describe it. New section 7 (`HookLock`): `locked_no_lost_update`,
`skipped_keeps_adapter`, `locked_schedules_reachable` (not vacuous), and the
pre-G8 counterexample `unlocked_loses_adapter_commit`. Timing on a scratch
file: 0.35 s with nothing to fix, 0.66 s with one fix (process start
included). Pins: `lib/tests/test_decisions_gtd_b.py::test_hook_*`.
