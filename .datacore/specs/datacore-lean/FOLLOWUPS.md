# Follow-ups after applying the decisions (2026-09-23)

The owner's choices on `DECISIONS.md` are in `decisions-2026-09-23.json`. The
local code changes are applied; per-area evidence is under "Decision … applied"
in `findings/`. What is left:

## Status after the follow-up board (2026-09-23, evening)

Owner choices: `followups-2026-09-23.json`. Applied and pushed:
datacore `2865806`, datacore-nightshift `887f3b7`, datacore-space `4fd68980`,
0-personal `42e817985`, datacore-dips `69c9c16`, org-workspace `711f2fc`
(tag v0.6.0, published to PyPI).

| Item | Status |
|---|---|
| Env overlap check on every host (C5) | Done: no key in both files on any of the 5 machines |
| Code rollout (C4) | winston and nightshift pulled (fast-forward); nightshift services restarted, both active. **hermes and plur-claw track their own forks** (tris-on-hermes/datacore, data-on-claw/data-space) and still run the older creds.py; they need the owner's usual manual update. Locks are per-machine, so a staggered rollout is safe. |
| Declared actor per host (L10) | winston=winston, nightshift=miles, hermes=tris, plur-claw=data, all declared in identity.env |
| Dispatcher actor (L4) | nightshift runs `ledger-claim.service` as miles, so items addressed to miles are still taken |
| id_churn --acknowledge (D1) | Done on mac, winston, nightshift. hermes and plur-claw wait for the rollout |
| Edit protocol 2 (L7) | **Blocked** until hermes and plur-claw run the new code |
| Research queue migration (D9) | Done: 31 properties moved; a re-run moves nothing |
| DIP edits (D5, G2, P7) | Done. DIP-0010 also had 8 wrong example weekdays, now fixed |
| SCAFFOLDING exposure (X1) | Untracked and ignored in 2-datacore |
| org-workspace 0.6.0 (G5) | Rebased onto the published 0.5.4 history first (the local clone was stale); 288 tests, coverage 80.24%; released |
| chief-of-staff | **Not committed**: the repo is on the owner's feature branch `feat/briefing-agents-the-ask-system-pulse`. Pending there: cos_generate.py (strict actor, Q2) and two test files |

New open questions from this round are in `findings/*.md` under "Decision … applied".

## A. Actions waiting (original list) for the owner's go-ahead (they leave this machine)

| # | Action | Blocked on |
|---|---|---|
| A1 | Distribute the new `creds.py` to all hosts in one run (C4) | Its lock file name changed, so old and new versions don't exclude each other. |
| A2 | On each host, run `env_overlap_report.py` and compare fingerprints (C5). mac: `python3 ~/Data/.datacore/lib/env_overlap_report.py`. Others: `ssh <host> 'python3 - --root ~/Data' < ~/Data/.datacore/lib/env_overlap_report.py` | Should run before A1. |
| A3 | Run `id_churn.py --acknowledge` on each host (D1) | Until then the legacy count baseline hides up to 360 (0-personal) and 271 (2-datacore) new orphans. |
| A4 | Release org-workspace 0.6.0 (G5) | **Hold.** The new default parser drops QUEUED/WORKING/FAILED, so `intent_sources.org_nodes` and every default-config reader would read a headerless retired state as heading text. First port `intent_sources` and its test to v2.0 and check live org files for headerless retired states. Versions also disagree: pyproject 0.5.2, installed 0.5.1, memory says 0.5.4 is on PyPI. |
| A5 | DIP edits in the dips repo, next to your uncommitted work: DIP-0002 (strike "Override values", D5); DIP-0009 (add DEFERRED→CANCELLED, G2); DIP-0010 (ConflictDetector/Resolver removed, P7) | The owner has uncommitted work in that repo. |
| A6 | Write `2` to `.datacore/ledger-edit-protocol` in the spaces (L7) | Only after every host runs the new code; an older reader treats a version-2 edit as a conflict and its state_root diverges. |
| A7 | Run `migrate_research_props.py 0-personal/org/research_learning.org --apply` (D9) | A dry run on a copy moved 31 properties across 17 items, with 0 duplicates. |
| A8 | Check which `DATACORE_ACTOR` the nightshift host's `ledger-dispatch.service` runs as (L4) | 5 open items are addressed to `miles`; since L4 only a dispatcher running as `miles` takes them. |
| A9 | Confirm L10 on each satellite: `this_actor(strict=True)` must resolve there | Otherwise that host refuses to append. The registry declares an actor for every server, but only the mac was checked. |

## B. New questions raised while applying

1. **2-datacore/SCAFFOLDING.md** (D6): this file is tracked, contains SPACE content, and its repo has the protected public `upstream` remote `datacore-one/datacore-org`. It now refuses to rebuild. Untrack and gitignore it, or is the public upstream intended? If intended, `SCAFFOLDING.space.md` is already exposed.
2. L2: nights where the invariants check can't tell now page (exit 2 is outside `exit_ok: [0,1]`). That matches the option chosen; confirm, or add 2 to `exit_ok`.
3. L10 callers that resolve identity non-strictly: move them to `this_actor(strict=True)` so they fail on startup, not at append. Includes nightshift `claim.py` and `ledger_hooks.py`, which use `socket.gethostname()` raw.
4. L7: should `projection_state` and `ledger_phase1_prepare` also detect changed fields type-strictly?
5. S1: the Bash `description` field is now matched too, so a description quoting an effect pattern pauses an innocent command. Exclude `description`?
6. S3: a repo with no remote-tracking refs has its whole push range judged. Acceptable?
7. P1: should the sweep's `git pull` also run under `_repo_lock`? P2: should a deletion refusal fail the run (exit 1)? P2: should the sweep fetch before the deletion check even without `--pull`?
8. P6: look up the canonical issue for duplicates (one extra API call each)?
9. N1: `--reopen` refuses REVIEW tasks in ledger-phase-1 spaces. Should it emit the ledger event itself?
10. N6: if `budget_exhausted` is ever reached inside execute, should run.py treat it as a deferral?
11. G8 manual repair tools (org_union_merge --apply, org_dedup_within_file, org_resolve_id_conflicts, inbox_dedup, stamp_seq_todo, validate_org_dates --fix): when should they move under the lock? Should `org_transaction.serialized` take a `timeout=`?
12. G9: in a Phase 1 generated next_actions.org, dedup now refuses to drop a copy with a distinct id. Should it dismiss via the ledger instead?
13. G12 residue: a theirs item with no parent and a level deeper than 1 still goes to end of file. Create a heading there too?
14. chief-of-staff `tests/test_cos_environment.py` needs a "compatible installed core" to run; 4 of its tests fail on this machine at HEAD as well. The C2 test added there could not be run here.
15. `modules/nightshift/lib/router.py:338-345` still calls an archived output the "artifact-landed signal"; P5 changed that.
