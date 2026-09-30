#!/usr/bin/env python3
"""Commit and push agent work that is trapped on one machine.

The counterpart to git_fleet_audit.py: the audit finds work that will never
reach anyone else, this lands it.

Why this exists: agents on the nightshift server edit their own module code
and never commit it. On 2026-07-12 that was 59 files across 12 repos, and it
had a concrete cost — Miles was BLOCKED since 2026-05-03 on a task because
`miles_bot.py` was modified on the server and never pushed, so it did not
exist in his workspace. He could not see his own bot's source.

Not a blind `git add -A`. Agents leave real junk behind, and committing it is
how a shared repo turns into a landfill:

  *.bak-<ts>      agents back up a file before editing it, then never clean up
  __pycache__     build artifacts
  *.local.*       the private layer of DIP-0002 — MUST NOT be shared
  root duplicates an untracked file at the repo root whose name collides with
                  a tracked lib/<name> — an agent wrote to the wrong path

Only touches repos already on their default branch. A repo on a feature branch
is either legitimate work-in-flight or a stranding case, and neither should be
resolved by a sweep — that needs a human decision.

Also refuses any branch with a review gate on it. This sweep lands work nobody
reviewed, so a branch that only changes through a reviewed PR is out of bounds
by definition — see review_gate(), and af1e8d9 for what happens without it.

Dry-run by default. Pass --execute to actually commit and push.

Before any push, two refusals (the work stays committed locally, and the run
exits 1 so the timer shows red -- decision Q7 keeps that exit status):
  * a ledger fork in what HEAD would publish (git_relay.publication_forks);
  * any commit in origin/<default>..HEAD that deletes a tracked file (decision
    P2, 2026-09-23). The sweep's own commit never deletes, but the push
    publishes the whole range; the commits and paths are printed for a human.
    origin/<default> is fetched first, with or without --pull (decision Q8),
    so the range is judged against origin as it is now; a fetch that fails is
    itself a refusal, named in the status and the summary.

With --execute, each repo's in-progress check, pull (fetch + merge, decision
Q6), inventory, commit, deletion-check fetch and push run in ONE critical
section under ledger_transport._repo_lock (decision P1), the lock the
transport and publications already take, so no two writers change one
repository at once. If the lock stays busy the repo is reported BUSY, nothing
is pulled, and it is left for the next run. The dry run takes no lock and
never pulls.

Sync is bidirectional: --pull also rebases each default-branch repo onto origin
first, so an agent ends the run BOTH visible to the others and on their latest
state. Pushing alone is not enough — Tris's tris-space was 195 commits behind
when this was written, so he was sharing a months-old view of the world.

Usage:
    python3 .datacore/lib/git_fleet_sync.py [data_dir] [--execute] [--pull]
                                            [--hold=repo1,repo2]

Persistent exclusion (alternatives to --hold that survive across runs):
    .datacore/config/sync-exclude.yaml   list under 'exclude:' key (repo names)
    DATACORE_SYNC_EXCLUDE env var        space-separated repo names
    .datacore-nosync in a repo root      per-repo opt-out marker file

Intended to run on a timer on every agent host (nightshift, hermes, plur-claw).
Without that, agents silently re-strand: neither hermes nor plur-claw had any
cron or timer touching git, which is why Tris accumulated 53 uncommitted files
over two months and nobody ever saw his research.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
import yaml

from git_inventory import changes as working_changes, tracked_paths
from context_merge import private_context_reason

# Filenames matching these are never committed.
JUNK_SUFFIXES = ('.pyc', '.orig', '.rej', '.swp')
JUNK_DIRS = ('__pycache__', '.pytest_cache', 'node_modules', '.venv',
             'dist', 'build')


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True)
    return (r.stdout or '').strip() if r.returncode == 0 else ''


def git_raw(repo: Path, *args: str) -> str:
    """Like git(), but preserves leading whitespace.

    `git status --porcelain` encodes staged/unstaged in columns 1-2, so a
    file modified but not staged reads ' M path' — with a LEADING SPACE.
    Stripping it shifts every column and silently eats the first character
    of the first path in the output.
    """
    r = subprocess.run(['git', *args], cwd=repo, capture_output=True, text=True)
    return (r.stdout or '').rstrip('\n') if r.returncode == 0 else ''


def default_branch(repo: Path) -> str:
    ref = git(repo, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')
    return ref.split('/', 1)[1] if ref.startswith('origin/') else 'main'


def github_slug(repo: Path) -> str:
    """Return 'owner/name' if origin is on github.com, else ''."""
    url = git(repo, 'remote', 'get-url', 'origin')
    for prefix in ('git@github.com:', 'https://github.com/', 'ssh://git@github.com/'):
        if url.startswith(prefix):
            return url[len(prefix):].removesuffix('.git').strip('/')
    return ''


def review_gate(repo: Path, branch: str) -> str:
    """Return a reason string if `branch` is review-gated, else ''.

    A blind sweep must never land an unreviewed commit on a branch a human
    agreed would only change through a reviewed PR. On 2026-08-10 this script
    committed a file straight onto plur-ai/enterprise `main` (af1e8d9) — that
    repo was in scope only because it sits under `*/2-projects/*`, and `main`
    was its default branch, so both guards above waved it through.

    Asked of the platform rather than a hardcoded list, because a list of
    "repos with a review gate" is wrong the day someone protects a new one.

    Refuses only when the gate admits NOBODY — PRs required and no bypass
    allowance — because then a direct push cannot land under any identity and
    committing locally just strands the work. When a bypass list exists the
    remote is the better judge than we are: it knows which identity is actually
    pushing, and this process does not. `gh` here may be authenticated as a
    different account than the SSH key that performs the push (on nightshift it
    is: `gh` is plur9, the key is miles-on-nightshift), so any actor check we
    did locally would be guessing. Let the push run — a permitted identity
    lands it, a gated one is rejected and reported as PUSH FAILED. Either way
    nothing unreviewed reaches a branch that forbids it.

    An inconclusive answer also proceeds: reading protection needs admin, and
    the datacore-one org is on a plan where protection cannot exist at all
    (403 "Upgrade to GitHub Pro" is the answer "not gated", not an unknown).
    Non-GitHub remotes have no gate to consult.
    """
    slug = github_slug(repo)
    if not slug:
        return ''

    for describe in (_classic_gate, _ruleset_gate):
        reason = describe(repo, slug, branch)
        if reason:
            return reason
    return ''


def _gh_json(repo: Path, path: str):
    """GET a gh api path, or None if it failed or was not JSON."""
    r = subprocess.run(['gh', 'api', path], cwd=repo,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except ValueError:
        return None


def _classic_gate(repo: Path, slug: str, branch: str) -> str:
    """Classic branch protection. Invisible to the rulesets endpoint."""
    prot = _gh_json(repo, f'repos/{slug}/branches/{branch}/protection')
    if not isinstance(prot, dict):
        return ''

    reviews = prot.get('required_pull_request_reviews')
    if not reviews:
        return ''

    allowances = reviews.get('bypass_pull_request_allowances') or {}
    if any(allowances.get(k) for k in ('users', 'teams', 'apps')):
        return ''

    return (f'{slug}@{branch} requires a PR (branch protection) and allows no '
            'bypass — a sweep cannot land here; open a PR')


def _ruleset_gate(repo: Path, slug: str, branch: str) -> str:
    """Repository rulesets. Invisible to the branch-protection endpoint.

    The two mechanisms are reported by two different endpoints and neither
    mentions the other: on plur-ai/enterprise, `main` (classic) reads as `[]`
    here, and `development` (ruleset) reads as 404 "Branch not protected"
    there. Consulting one and concluding "unguarded" is how a sweep talks
    itself into a push it should not make.
    """
    rules = _gh_json(repo, f'repos/{slug}/rules/branches/{branch}')
    if not isinstance(rules, list):
        return ''

    ids = {r.get('ruleset_id') for r in rules
           if isinstance(r, dict) and r.get('type') == 'pull_request'}
    if not ids:
        return ''

    # This endpoint reports a rule whether or not the caller bypasses it, so
    # the bypass list has to be read off each ruleset directly. Same policy as
    # the classic path: any bypass at all means the remote is the better judge
    # of whether THIS push is allowed, because it knows the pushing identity
    # and we do not.
    for rid in ids:
        rs = _gh_json(repo, f'repos/{slug}/rulesets/{rid}')
        if not isinstance(rs, dict):
            return ''
        if rs.get('bypass_actors'):
            return ''

    return (f'{slug}@{branch} requires a PR (repository ruleset) and allows no '
            'bypass — a sweep cannot land here; open a PR')


def is_junk(repo: Path, path: str, tracked: set) -> str:
    """Return a reason string if this path should not be committed, else ''."""
    p = Path(path)
    name = p.name

    if any(part in JUNK_DIRS for part in p.parts):
        return 'build artifact'
    if name.endswith(JUNK_SUFFIXES):
        return 'build artifact'
    # Agent backup files: miles_bot.py.bak-20260627120138
    if '.bak' in name:
        return 'agent backup file'
    # DIP-0002 private layer — never leaves the machine.
    if '.local.' in name:
        return 'private layer (DIP-0002)'
    # A composed context file (context_merge output) -- generated per machine,
    # and it may carry the private layer (SPC-3).
    private = private_context_reason(repo / path.rstrip('/'), tracked=path in tracked)
    if private:
        return private
    # An untracked file at the repo root that shadows a tracked lib/<name>:
    # an agent wrote to the wrong path. Committing it creates a second,
    # divergent copy of a module that is already tracked under lib/.
    if len(p.parts) == 1 and path not in tracked:
        if f'lib/{name}' in tracked:
            return f'duplicate of lib/{name}'

    # A nested git repo that is not a registered submodule. Committing this
    # embeds a bare gitlink with no .gitmodules entry — a pointer to a commit
    # nobody can resolve. plur-space carries two of these already (`plur`,
    # `website`); they are why its diff shows phantom "modified" entries.
    full = repo / path.rstrip('/')
    if (full / '.git').exists():
        return 'nested git repo, not a submodule'

    return ''


def has_conflict_markers(full: Path) -> bool:
    """Does this file carry leftover merge-conflict markers (SYN-3)?

    Both a `<<<<<<< ` and a `>>>>>>> ` line, at line starts: one alone is
    ordinary text (a quoted diff, an ASCII rule). The in-progress guard only
    sees a merge git is still in; markers left in a file after a merge was
    aborted, or copied into a new note, reach here as ordinary changes.
    """
    try:
        if not full.is_file() or full.stat().st_size >= 50 * 1024 * 1024:
            return False
        data = full.read_bytes()
    except OSError:
        return False
    lines = data.split(b'\n')
    return (any(line.startswith(b'<<<<<<< ') for line in lines)
            and any(line.startswith(b'>>>>>>> ') for line in lines))


def in_progress(repo: Path) -> str:
    """'merge' | 'rebase' | 'cherry-pick' | 'revert' | '' for this checkout.

    ASK GIT WHERE ITS STATE LIVES. `repo/.git/MERGE_HEAD` assumes `.git` is a
    directory; in a linked worktree or a submodule `.git` is a FILE pointing
    elsewhere, so that path never exists and the guard never fired. The pull
    below then failed with "You have not concluded your merge" and its
    failure path ran `git merge --abort`, discarding a human's hand
    resolution (replayed: tests/test_git_fleet_formal.py). An unanswerable
    question is reported as in progress: this sweep must not guess.
    """
    for marker, name in (('MERGE_HEAD', 'merge'), ('rebase-merge', 'rebase'),
                         ('rebase-apply', 'rebase'), ('CHERRY_PICK_HEAD', 'cherry-pick'),
                         ('REVERT_HEAD', 'revert')):
        r = subprocess.run(['git', 'rev-parse', '--git-path', marker], cwd=repo,
                           capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            return 'unknown git state'
        path = Path(r.stdout.strip())
        if not path.is_absolute():
            path = repo / path
        if path.exists():
            return name
    return ''


def scheduled_run_active() -> bool:
    """Does a scheduled run (nightshift's run.py) hold its run lock right now?

    run.py holds ~/.datacore/state/nightshift-run.lock (flock, exclusive) for
    the whole overnight run. Probed with a shared, non-blocking lock that is
    released at once, so this never keeps a run from starting.
    """
    import fcntl
    path = Path.home() / '.datacore' / 'state' / 'nightshift-run.lock'
    try:
        with open(path, 'r') as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except OSError:
                return True
            fcntl.flock(fh, fcntl.LOCK_UN)
    except OSError:
        return False                # no lock file: no run has ever held it here
    return False


def sync_repo(repo: Path, execute: bool, hold: tuple = (), pull: bool = False) -> dict:
    branch = git(repo, 'branch', '--show-current')
    default = default_branch(repo)

    result = {'name': repo.name, 'branch': branch, 'default': default,
              'skipped': [], 'committed': [], 'status': '', 'pull': ''}

    if repo.name in hold:
        result['status'] = 'SKIP — held back explicitly (--hold)'
        return result

    # Per-repo opt-out: a .datacore-nosync marker in the repo root skips it
    # from both pull and push without requiring any central config update.
    if (repo / '.datacore-nosync').exists():
        result['status'] = 'SKIP — .datacore-nosync marker present'
        return result

    if not branch:
        result['status'] = 'SKIP — detached HEAD'
        return result
    if branch != default:
        result['status'] = f'SKIP — on {branch}, not {default} (needs a decision)'
        return result

    gated = review_gate(repo, default)
    if gated:
        result['status'] = f'SKIP — {gated}'
        return result

    if execute and scheduled_run_active():
        # NOTHING CHANGES UNDER A RUNNING SCHEDULED RUN (MEM-65). A pull here
        # moved nightshift's checkout to new code mid-run, and a commit would
        # capture files the run is still writing. Report it; the next run
        # (every sweep is idempotent) does the work once the run has ended.
        result['status'] = ('BUSY — a scheduled run holds its run lock '
                            '(nightshift-run.lock); nothing pulled or committed, next run retries')
        return result

    if not execute:
        # The dry run takes no lock and never pulls.
        busy = in_progress(repo)
        if busy:
            result['status'] = f'SKIP — {busy} in progress; resolve first'
            return result
        return _land(repo, result, execute, default)
    # ONE WRITER PER REPOSITORY (decisions P1 and Q6). ledger_transport's
    # converge and publish, and publication_state.reserve, all stage and
    # commit in these same checkouts under _repo_lock; this sweep did not take
    # it, which is the race behind the 2026-09-16 stranded publication record,
    # left open from this side. Same lock, same key (the resolved
    # git-common-dir), held ONCE per repository from the in-progress check
    # through the pull (fetch + merge, Q6), the inventory, the commit, the
    # deletion check's fetch (Q8) and the push: a merge is as much a write to
    # the index and the branch as a commit is, and another writer's commit
    # between our pull and our inventory is what the one section excludes.
    # None of those holders nests it and this does not either (a same-thread
    # re-entry is a no-op), so there is no cycle to deadlock on:
    # DatacoreSpec/Publication.lean §5 (`lock_mutual_exclusion`,
    # `lock_no_deadlock`, `holder_stable`).
    from ledger_transport import _repo_lock
    entered = False
    try:
        with _repo_lock(repo):
            entered = True
            # Never commit or stage during an in-progress merge or rebase.
            # A sweep that fires mid-conflict stages marker-laden files and pushes
            # them to shared remotes — observed 2026-06-09 (issue #28): a background
            # sync committed literal <<<<<<</<=======/>>>>>>> markers to two files on
            # origin/main of a shared repo while a hand-resolution was in progress.
            busy = in_progress(repo)
            if busy:
                result['status'] = f'SKIP — {busy} in progress; resolve first'
                return result
            if pull:
                _pull(repo, result, default)
            return _land(repo, result, execute, default)
    except TimeoutError:
        if entered:
            raise
        result['status'] = ("BUSY — another writer holds this repository's lock; "
                            "nothing pulled, work left as it was, next run retries")
        return result


#: A LARGE LOSS IS HELD, NEVER PROPAGATED SILENTLY (MEM-61). Deleting this many
#: tracked files in one range, or shrinking a file of at least SHRINK_MIN_LINES
#: to SHRINK_KEEP or less of its lines, stops the sync for that repo and fails
#: the run (the alert fires); the files stay as they are here. A human accepts a
#: loss that was meant by merging it by hand.
MASS_DELETION = 10
SHRINK_MIN_LINES = 50
SHRINK_KEEP = 0.2


def _lines(text: str | None) -> int:
    return len(text.splitlines()) if text else 0


def drastic_shrink(old_lines: int, new_lines: int) -> bool:
    return old_lines >= SHRINK_MIN_LINES and new_lines <= old_lines * SHRINK_KEEP


def incoming_losses(repo: Path, ref: str) -> list[str]:
    """What merging `ref` would delete or drastically shrink here (MEM-61).

    Judged on origin's side of the merge only (merge-base..ref): another
    machine's commits that lack 25 files, or cut a 400-line file to one line,
    arrive as an ordinary clean merge and remove the files here without a
    word. [] means nothing large is lost.
    """
    base = git(repo, 'merge-base', 'HEAD', ref)
    if not base:
        return []
    deleted = [x for x in git(repo, '-c', 'core.quotepath=off', 'diff', '--no-renames',
                              '--diff-filter=D', '--name-only', base, ref).splitlines() if x]
    out = []
    if len(deleted) >= MASS_DELETION:
        out.append(f"deletes {len(deleted)} tracked files ({', '.join(deleted[:5])}"
                   f"{', …' if len(deleted) > 5 else ''})")
    for row in git(repo, '-c', 'core.quotepath=off', 'diff', '--no-renames', '--numstat',
                   '--diff-filter=M', base, ref).splitlines():
        added, removed, path = (row.split('\t', 2) + ['', '', ''])[:3]
        if not removed.isdigit() or int(removed) < SHRINK_MIN_LINES * (1 - SHRINK_KEEP):
            continue
        old_n = _lines(git_raw(repo, 'show', f'{base}:{path}'))
        new_n = _lines(git_raw(repo, 'show', f'{ref}:{path}'))
        if drastic_shrink(old_n, new_n):
            out.append(f"shrinks {path} from {old_n} to {new_n} lines")
    return out


#: What git prints when the remote refused THIS host, as opposed to not
#: answering. Checked before the network words: both kinds end with git's
#: generic "Please make sure you have the correct access rights", which it
#: prints for EVERY ssh failure -- matching on that sentence reported an
#: unreachable host as a credential problem (SYN-6).
_DENIED = ('permission denied', 'authentication failed', 'could not read username',
           'error: 403', 'error: 401', '403 forbidden', 'repository not found',
           'host key verification failed')
_OFFLINE = ('timed out', 'could not resolve', 'connection refused', 'network is unreachable',
            'no route to host', 'connection reset', 'connection closed', 'name resolution',
            'failed to connect', 'could not connect', 'nodename nor servname')


def failure_kind(out: str) -> str:
    """'denied' | 'offline' | '' for a failed fetch/pull/push's output."""
    low = (out or '').lower()
    if any(s in low for s in _DENIED):
        return 'denied'
    if any(s in low for s in _OFFLINE):
        return 'offline'
    if 'could not read from remote repository' in low or 'unable to access' in low:
        return 'offline'            # the remote did not answer; nothing said "denied"
    return ''


def _pull(repo: Path, result: dict, default: str) -> None:
    """Fetch and merge origin/<default>. Runs under _repo_lock (decision Q6).

    Sync is bidirectional. Pushing agent work out is only half of it — an agent
    that never pulls drifts onto a stale snapshot of shared knowledge and stops
    seeing anyone else's. Tris's tris-space was 195 commits behind when this was
    written, so he was "sharing" a months-old view of the world.
    """
    subprocess.run(['git', 'fetch', '-q', 'origin'], cwd=repo, capture_output=True)
    losses = incoming_losses(repo, f'origin/{default}')
    if losses:
        result['incoming_loss'] = losses
        result['pull'] = ('PULL REFUSED — origin/' + default + ' ' + '; '.join(losses)
                          + ' — kept here; a human merges it by hand if it was meant')[:300]
        return
    # MERGE, NEVER REBASE (DIP-0046). Rebase rewrites this box's local
    # commits to sit on top of origin, which gives them new hashes. If the
    # subsequent push then fails — offline, gated, rejected — those commits
    # exist under an identity nothing else has seen, and the next run's
    # watchdog treats them as junk to reset past. That is how 23 commits in
    # 2-datacore and 27 in 3-fds were stranded on 2026-08-12.
    #
    # A merge cannot do this: local commits keep their hashes and stay
    # reachable no matter how many times the push fails afterwards. And for
    # the per-writer event logs this exists to move, a merge is a union of
    # disjoint files — there is nothing for it to conflict over.
    r = subprocess.run(['git', 'pull', '--no-rebase', 'origin', default],
                       cwd=repo, capture_output=True, text=True)
    if r.returncode != 0 and _keep_conflict(repo, result, r):
        return
    if r.returncode != 0:
        # Never leave a half-applied merge behind for the next run to trip on.
        subprocess.run(['git', 'merge', '--abort'], cwd=repo, capture_output=True)
        # Keep the 'PULL CONFLICT' prefix — the summary filters on it — but
        # carry the actual error: on 2026-08-28 a transient failure (not a
        # conflict) wore this label through three runs on plur-claw, and the
        # discarded stderr was the only thing that could have said so.
        out = (r.stderr or '') + (r.stdout or '')
        detail = out.strip().splitlines()
        tail = detail[-1][:120] if detail else ''
        # A PULL that fails for want of ACCESS is not a conflict, and
        # calling it one sends someone hunting a merge that does not
        # exist. On 2026-08-30 three repos (DHF, website, extract-cli)
        # were reported as 'pull conflicts' when this host's key simply
        # is not authorised for them — a credential job, not a merge job.
        # `error: 403` / `error: 401` are the forms git's HTTP transport
        # actually emits ("The requested URL returned error: 403"); the
        # literal '403 Forbidden' never appears there. Six module repos on
        # winston were therefore reported as PULL CONFLICT on 2026-08-31 —
        # sending someone to resolve a merge that does not exist — for
        # exactly the credential reason this branch was written to catch.
        kind = failure_kind(out)
        if kind == 'offline':
            # OFFLINE IS NOT DENIED. A host that cannot reach the remote right
            # now needs waiting, not a key: "NO ACCESS — credential job" sent
            # people to regenerate keys for a laptop on a train (SYN-6).
            first = next((l for l in detail if l.strip()), '')[:120]
            result['pull'] = (f'OFFLINE — this host could not reach the remote '
                              f'(network/timeout); local work is kept and retried '
                              f'next run [{first}]')
        elif kind == 'denied':
            # Distinguish "stale" from "work at risk". A host that cannot
            # reach a remote it has nothing to send is merely behind; a
            # host holding unpushed commits it cannot push has work
            # nobody else can see. Only the second is a failure — a check
            # that is permanently red is one you learn to ignore, which is
            # the same lesson the watchdog learned the hard way.
            # `@{u}` CANNOT ANSWER THIS FROM HERE. A host that cannot fetch
            # can never update its remote-tracking ref, so the count is
            # frozen at whatever it was when access last worked — and it
            # over-reports forever once another machine lands the work.
            # Measured 2026-08-31: winston reported 5 module repos holding
            # unpushed commits; checked from the Mac, which can reach
            # GitHub, ALL FIVE HEADs were already ancestors of origin. The
            # run failed on all of them, so its check could never go green.
            #
            # This is the same defect git_relay.py was rewritten to remove
            # ("it kept reporting 166 commits as trapped after every one of
            # them had been relayed"). The answer there was to ask the
            # machine that CAN see the remote — which is git_relay's job,
            # not this one's. So report the access gap and defer the
            # at-risk question rather than guessing it from a stale ref.
            unpushed = git_raw(repo, 'rev-list', '--count', '@{u}..HEAD') or '0'
            n = unpushed.strip() if unpushed.strip().isdigit() else '?'
            result['pull'] = (
                f'NO ACCESS — this host cannot reach the remote '
                f'(local ref says {n} unpushed, UNVERIFIABLE from here — '
                f'run git_relay.py --check from the operator machine) '
                f'[{tail}]')
            result['access_at_risk'] = False
        elif 'refusing to merge unrelated histories' in out:
            result['pull'] = ('UNRELATED HISTORY — local checkout shares no '
                              'ancestor with origin; re-clone or align '
                              'deliberately')
        else:
            result['pull'] = f'PULL CONFLICT — needs a human [{tail}]'
    else:
        result['pull'] = 'pulled'
        _name_waiting(repo, result)


def _keep_conflict(repo: Path, result: dict, r: subprocess.CompletedProcess) -> bool:
    """ONE CONFLICT NEVER STOPS THE SPACE (SYN-9). On a knowledge repository,
    finish the merge the way converge does (ledger_transport.resolve_in_progress:
    both versions kept in the file, no copies, no side branch), file one task per
    conflicted file, and let the rest of the sweep land and push. True when the
    merge completed; False leaves the old path (abort, PULL CONFLICT) to run.
    Code repositories are never resolved here: they change by pull request."""
    if not direct_publication(repo):
        return False
    try:
        from ledger_transport import resolve_in_progress, file_conflict_tasks
        detail = ((r.stdout or '') + (r.stderr or '')).strip()
        ok, _, _, kept = resolve_in_progress(repo, detail, keep_both=True)
        if not ok:
            return False
        file_conflict_tasks(repo, kept)
    except Exception:  # noqa: BLE001 -- fall back to the abort path, which names it
        return False
    result['pull'] = 'pulled'
    _name_waiting(repo, result)
    return True


def _name_waiting(repo: Path, result: dict) -> None:
    """Conflicts still waiting for a person, named on every sweep until settled."""
    if not direct_publication(repo):
        return
    try:
        from ledger_transport import waiting_conflicts
        waiting = waiting_conflicts(repo)
    except Exception:  # noqa: BLE001
        return
    if waiting:
        result['conflicts_waiting'] = waiting



def fetch_default(repo: Path, default: str):
    """Refresh origin/<default> from origin. None on success, else why not.

    Named refspec, so the remote-tracking ref moves even on a git whose
    `git fetch origin <branch>` would only write FETCH_HEAD. Runs under
    _repo_lock (it writes a ref). Decision Q8, 2026-09-23.
    """
    try:
        r = subprocess.run(['git', 'fetch', '-q', 'origin',
                            f'+refs/heads/{default}:refs/remotes/origin/{default}'],
                           cwd=repo, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return 'git fetch timed out'
    if r.returncode:
        text = ((r.stderr or '') + (r.stdout or '')).strip()
        lines = [l for l in text.splitlines() if l.strip()]
        kind = failure_kind(text)
        if kind and lines:
            # Name the cause, from the line that states it (SYN-6).
            return f"{'offline' if kind == 'offline' else 'access denied'} — {lines[0][:120]}"
        return (lines[-1][:120] if lines else f'git fetch exited {r.returncode}')
    return None


def range_deletions(repo: Path, ref: str, commit: str):
    """Commits in `ref..commit` that delete a tracked file: [(sha, [paths])].

    None when git cannot list the range (no such ref, ...): unknown, so the
    caller refuses. `--cc` shows a merge's own deletions only (a path the
    result lacks though a parent had it and the merge did not simply take the
    other parent's side), so a deletion made on origin and merged in here is
    not counted: origin already has it. `--no-renames` counts a rename as the
    deletion it contains, as the sweep itself does.
    """
    r = subprocess.run(['git', '-c', 'core.quotepath=off', 'log', '--no-renames',
                        '--diff-filter=D', '--name-only', '--cc', '--format=%x01%H',
                        f'{ref}..{commit}'], cwd=repo, capture_output=True, text=True)
    if r.returncode:
        return None
    found = []
    for line in r.stdout.splitlines():
        if line.startswith('\x01'):
            found.append((line[1:], []))
        elif line.strip() and found:
            found[-1][1].append(line)
    return [(sha, paths) for sha, paths in found if paths]


#: Subject of the sweep's own commit (below). A commit with this subject is the
#: sweep's, from this run or an earlier one whose push was held.
SWEEP_SUBJECT = 'sync: land agent work trapped on this machine'


def direct_publication(repo: Path) -> bool:
    """True for knowledge/agent-personal repos: their default branch is where work lands.

    Anything else -- code, or a repository the registry cannot classify -- takes
    commits only through a reviewed pull request (DIP-0046; owner rule: agents
    never merge). Unknown is not knowledge.
    """
    try:
        from ledger_transport import classify
        result = classify(repo)
    except Exception:  # noqa: BLE001
        return False
    return bool(result.ok and result.reason in ('knowledge', 'agent-personal'))


def foreign_commits(repo: Path, default: str, tip: str = 'HEAD') -> list[str]:
    """Non-merge commits on `tip` that origin/<default> lacks and this sweep did not make.

    2026-09-27: an overnight task committed 93f3879 onto the nightshift host's
    local main of the public root repository. The sweep lands UNCOMMITTED work;
    a commit already on the branch is somebody's decision, and on a code
    repository that decision is a pull request the owner merges. Pushing HEAD
    here would have published it unreviewed along with the sweep's own commit.
    Merges are skipped: they are the sweep's own pulls of origin.
    """
    origin = f'refs/remotes/origin/{default}'
    if not git(repo, 'rev-parse', '--verify', '-q', origin):
        return []
    rows = git(repo, 'log', '--no-merges', '--format=%H %s', tip, f'^{origin}')
    commits = [line.split(' ', 1) + [''] for line in rows.splitlines() if line]
    return [f'{sha[:12]} {subject[:70]}' for sha, subject, *_ in commits
            if not subject.startswith(SWEEP_SUBJECT)]


def _hold_foreign(repo: Path, result: dict, default: str, foreign: list[str], prefix: str) -> dict:
    result['foreign'] = foreign
    result['status'] = (f"{prefix} — {len(foreign)} commit(s) on {default} that origin lacks and "
                        f"this sweep did not make; not pushed (a code repository changes by "
                        f"pull request): " + '; '.join(foreign))[:240]
    return result


def _land(repo: Path, result: dict, execute: bool, default: str) -> dict:
    """Inventory, stage, commit, gate and push. Runs under _repo_lock when executing."""
    try:
        inventory = working_changes(repo)
        tracked = tracked_paths(repo)
    except RuntimeError:
        result['status'] = 'INVENTORY FAILED — existing work retained; no publication attempted'
        return result
    code = not direct_publication(repo)
    if not inventory:
        foreign = foreign_commits(repo, default) if code else []
        if foreign:
            return _hold_foreign(repo, result, default, foreign, 'HELD')
        pulled = result.get('pull')
        # "clean" only when nothing failed: a pull that did not happen is not
        # a clean sync, whatever the working tree looks like (SYN-6).
        result['status'] = ('clean' + (f" ({pulled})" if pulled else '')
                            if not pulled or pulled == 'pulled'
                            else f'nothing to send; pull failed: {pulled}')
        return result

    to_add = []
    for change in inventory:
        xy, path = change.status, change.path
        if 'R' in xy:
            # A rename includes a deletion; this sweep has no authority to
            # infer that either side may replace the other on every host.
            result['skipped'].append((path, 'RENAME — requires explicit review'))
            continue
        # A "land trapped work" sweep must NEVER propagate a deletion. A tracked
        # file missing on one host is almost always a local defect — an incomplete
        # checkout, a crashed process, an agent that removed it — not work to
        # broadcast. `git add`-ing a ' D' porcelain entry stages the deletion and
        # pushes it to everyone: this is exactly how caa7d58 wiped 110 files from
        # datafund-space on 2026-07-21. Skip deletions; surface them for a human.
        if 'D' in xy:
            result['skipped'].append((path, 'DELETION — not auto-committed (needs a human)'))
            continue
        # Unmerged files (UU, AU, UA, DD, DU, UD, AA) contain conflict markers or
        # represent an unresolved state — committing them corrupts the repo.
        # The MERGE_HEAD guard above catches the common case; this is a
        # belt-and-suspenders catch for any unmerged path that slips through
        # (e.g. `git add -u` already ran on some files before the guard fired).
        if change.unmerged:
            result['skipped'].append((path, 'MERGE CONFLICT — unresolved; resolve before syncing'))
            continue
        reason = is_junk(repo, path, tracked)
        if reason:
            result['skipped'].append((path, reason))
            continue
        if has_conflict_markers(repo / path):
            result['skipped'].append((path, 'CONFLICT MARKERS — resolve before syncing'))
            continue
        # GitHub's hard push limit is 100 MB; warn and skip anything ≥50 MB so
        # there is headroom before a binary artifact causes a blocked push.
        # Observed 2026-06-11: knowledge.db hit 100 MB and blocked ALL pushes.
        full_path = repo / path
        if full_path.is_file():
            size_mb = full_path.stat().st_size / (1024 * 1024)
            if size_mb >= 50:
                result['skipped'].append(
                    (path, f'oversized ({size_mb:.1f} MB ≥ 50 MB limit) — add to .gitignore'))
                continue
            # A TRACKED FILE CUT TO A FRACTION IS NOT PUBLISHED (MEM-61): a
            # truncated write or a bad edit on this one machine would replace
            # the file everywhere. Held here, named, and the run fails.
            if path in tracked:
                old_n = _lines(git_raw(repo, 'show', f'HEAD:{path}'))
                try:
                    new_n = _lines(full_path.read_text(errors='replace'))
                except OSError:
                    new_n = old_n
                if drastic_shrink(old_n, new_n):
                    result['skipped'].append(
                        (path, f'SHRUNK from {old_n} to {new_n} lines — not published; a human decides'))
                    result.setdefault('shrunk', []).append(f'{path} ({old_n} -> {new_n} lines)')
                    continue
        to_add.append(path)

    if not to_add:
        result['status'] = 'nothing to commit (all junk)'
        return result

    result['committed'] = to_add

    if not execute:
        result['status'] = f'WOULD commit {len(to_add)}, skip {len(result["skipped"])}'
        return result

    for f in to_add:
        staged = subprocess.run(['git', '--literal-pathspecs', 'add', '--', f],
                                cwd=repo, capture_output=True)
        if staged.returncode:
            result['status'] = 'STAGING FAILED — existing work retained; no publication attempted'
            return result

    msg = (
        f"sync: land agent work trapped on this machine ({len(to_add)} files)\n\n"
        "Committed by git_fleet_sync. These changes were made by agents on this\n"
        "host and never committed, so they existed on exactly one disk and were\n"
        "invisible to every other agent.\n\n"
        f"Skipped as junk: {len(result['skipped'])} file(s).\n"
    )
    c = subprocess.run(['git', '--literal-pathspecs', 'commit', '-m', msg, '--', *to_add], cwd=repo,
                       capture_output=True, text=True)
    if c.returncode != 0:
        # A REFUSED FILE IS HELD BACK, NOT THE REPO (SYN-8). Ask the hook which
        # files it refuses (ledger_transport.refused_by_hook), name them, and
        # land the rest: one invalid org tag must not strand every other
        # change, and the history behind it, on this disk.
        from ledger_transport import refused_by_hook
        refused, said = refused_by_hook(repo, to_add)
        why = ((said or c.stderr or c.stdout or '').strip().splitlines() or [''])[0][:120]
        rest = [f for f in to_add if f not in refused]
        subprocess.run(['git', '--literal-pathspecs', 'restore', '--staged', '--', *refused],
                       cwd=repo, capture_output=True)
        for f in refused:
            result['skipped'].append((f, f'REFUSED by pre-commit hook — {why}'))
        result['hook_refused'] = refused
        c = None
        if rest:
            c = subprocess.run(['git', '--literal-pathspecs', 'commit', '-m', msg, '--', *rest],
                               cwd=repo, capture_output=True, text=True)
        if c is None or c.returncode != 0:
            result['committed'] = []
            result['status'] = (f"COMMIT FAILED: {why}" if c is None
                                else f"COMMIT FAILED: {(c.stderr or '').strip()[:120]}")
            return result
        to_add = rest
        result['committed'] = rest

    from git_publication import push_arguments
    captured = subprocess.run(['git', 'rev-parse', '--verify', 'HEAD^{commit}'],
                              cwd=repo, capture_output=True, text=True, timeout=30)
    if captured.returncode:
        result['status'] = 'committed; publication identity unavailable'
        return result
    try:
        args = push_arguments(captured.stdout.strip(), f'refs/heads/{default}')
    except ValueError:
        result['status'] = 'committed; publication identity invalid'
        return result
    # NEVER PUSH A FORK — the same rule git_relay enforces, applied to what
    # this push publishes: the whole of HEAD, not just this sweep's files.
    # A log rewound and re-appended on this disk, or a forked merge left on
    # the branch by anything else, is a valid-looking chain that replaces
    # (actor, seq) events every other machine already holds.
    from git_relay import publication_forks
    forks = publication_forks(repo, captured.stdout.strip(), f'origin/{default}')
    if forks:
        result['ledger_fork'] = forks
        result['status'] = ('committed, PUSH REFUSED — ledger fork: '
                            + '; '.join(forks)[:160])
        return result
    # NEVER PUBLISH A DELETION (decision P2). The sweep's own commit never
    # deletes (GitFleet.lean `sweep_never_deletes`), but the push publishes
    # the whole of origin/<default>..HEAD, and an earlier local commit that
    # removed tracked files would go out with it -- the caa7d58 shape. Name
    # the commits and paths; the work stays committed here for a human.
    #
    # FETCH FIRST (decision Q8), with or without --pull: the range is judged
    # against origin as it is NOW, not a remote-tracking ref this host last
    # moved days ago. A stale ref put origin's own deletions (already
    # published, merged here some other way) into the range, a false refusal.
    # A fetch that fails leaves nothing to judge against: refuse, and say so.
    fetch_error = fetch_default(repo, default)
    if fetch_error is not None:
        result['deletions'] = [('?', [f'cannot fetch origin/{default} to check the range '
                                      f'for deletions: {fetch_error}'])]
        result['status'] = (f'committed, PUSH REFUSED — cannot fetch origin/{default} '
                            f'to check the range for deletions: {fetch_error}')[:200]
        return result
    deletions = range_deletions(repo, f'origin/{default}', captured.stdout.strip())
    if deletions is None:
        result['deletions'] = [('?', [f'cannot list origin/{default}..HEAD'])]
    elif deletions:
        result['deletions'] = deletions
    if result.get('deletions'):
        named = '; '.join(f"{sha[:12]}: {', '.join(paths)}" for sha, paths in result['deletions'])
        result['status'] = ('committed, PUSH REFUSED — the range deletes tracked files: '
                            + named)[:200]
        return result
    # (After the deletion check, which names the more specific refusal.)
    # NEVER PUBLISH A COMMIT THIS SWEEP DID NOT MAKE onto a code repository's
    # default branch (2026-09-27, 93f3879). Judged against the origin just
    # fetched; the sweep's own commit stays here with the rest, for a human.
    foreign = foreign_commits(repo, default, captured.stdout.strip()) if code else []
    if foreign:
        return _hold_foreign(repo, result, default, foreign, 'committed, PUSH REFUSED')
    p = subprocess.run(['git', *args], cwd=repo,
                       capture_output=True, text=True)
    if p.returncode != 0:
        result['status'] = f"committed, PUSH FAILED: {(p.stderr or '').strip()[:120]}"
        return result

    result['status'] = f'PUSHED {len(to_add)} file(s) to origin/{default}'
    return result


#: Unpushed work older than this is stranded: named, and the run fails (SYN-5).
STRANDED_AFTER_S = 24 * 3600


def stranded_branches(repo: Path, now: float | None = None) -> list[tuple[str, int, float]]:
    """Local branches holding commits that are on no remote, oldest > a day.

    [(branch, commits, oldest commit time)]. Every local branch, not only the
    checked-out one: work committed on `agent/draft` while the checkout is back
    on main is invisible to the sweep, which only ever looks at HEAD, and was
    reported nowhere (SYN-5). Fresh unpushed work stays quiet -- a branch an
    agent is still working on is not stuck yet.
    """
    import time as _time
    now = _time.time() if now is None else now
    out = []
    for branch in git(repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads/').splitlines():
        branch = branch.strip()
        if not branch:
            continue
        times = git(repo, 'log', '--format=%ct', f'refs/heads/{branch}', '--not', '--remotes').split()
        stamps = [int(t) for t in times if t.isdigit()]
        if stamps and now - min(stamps) > STRANDED_AFTER_S:
            out.append((branch, len(stamps), float(min(stamps))))
    return out


def find_repos(root: Path) -> list:
    repos = []
    if (root / '.git').exists():
        repos.append(root)
    for sub in sorted(root.iterdir()):
        if sub.is_dir() and not sub.name.startswith('.') and (sub / '.git').exists():
            repos.append(sub)
    for pattern in ('.datacore/modules/*/.git', '*/2-projects/*/.git'):
        for gitdir in root.glob(pattern):
            repos.append(gitdir.parent)
    return sorted(set(repos))


def load_sync_exclude(root: Path) -> tuple:
    """Load the persistent exclusion list from config file and env var.

    Returns a tuple of repo names to skip from ALL sync operations (neither
    pull nor push). These are merged with any --hold= flag at runtime.

    Sources checked (all merged):
      .datacore/config/sync-exclude.yaml  —  list under the 'exclude:' key
      DATACORE_SYNC_EXCLUDE env var        —  space-separated repo names
    """
    excluded: set = set()
    config_path = root / '.datacore' / 'config' / 'sync-exclude.yaml'
    if config_path.exists():
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        for entry in cfg.get('exclude', []):
            if entry:
                excluded.add(str(entry).strip())
    for entry in os.environ.get('DATACORE_SYNC_EXCLUDE', '').split():
        if entry:
            excluded.add(entry)
    return tuple(excluded)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    execute = '--execute' in sys.argv
    pull = '--pull' in sys.argv
    root = Path(args[0]).expanduser() if args else Path.home() / 'Data'

    hold = ()
    for a in sys.argv[1:]:
        if a.startswith('--hold='):
            hold = tuple(x.strip() for x in a.split('=', 1)[1].split(',') if x.strip())

    hold = hold + load_sync_exclude(root)

    if not execute:
        print("DRY RUN — nothing will be committed. Pass --execute to act.\n")

    repos = find_repos(root)
    results = [sync_repo(r, execute, hold, pull) for r in repos]

    total_c = total_s = 0
    for r in results:
        if r['status'] in ('clean',) or r['status'].startswith('SKIP'):
            continue
        print(f"{r['name']}  [{r['branch']}]")
        print(f"  {r['status']}")
        for f in r['committed']:
            print(f"    + {f}")
        for f, why in r['skipped']:
            print(f"    - {f}  ({why})")
        print()
        total_c += len(r['committed'])
        total_s += len(r['skipped'])

    conflicts = [r for r in results if r.get('pull', '').startswith('PULL CONFLICT')]
    if conflicts:
        print('Pull conflicts — these agents are NOT on latest and need a human:')
        for r in conflicts:
            print(f"  {r['name']}")
        print()

    # A conflict the sweep merged around (SYN-9) is not a failure of the run:
    # the space synced, and the person has a task. Named every run until settled.
    waiting = [r for r in results if r.get('conflicts_waiting')]
    if waiting:
        print('Conflicts waiting for a person — everything else in each space synced:')
        for r in waiting:
            for path, task in r['conflicts_waiting']:
                print(f"  {r['name']}: {path} (both versions kept in the file; task {task})")
        print()

    # Access failures are reported SEPARATELY from conflicts: they need a
    # credential, not a merge, and grouping them taught the reader to look
    # for the wrong thing entirely.
    noaccess = [r for r in results if r.get('pull', '').startswith('NO ACCESS')]
    if noaccess:
        print('No access — this host cannot reach these remotes '
              '(credential/deploy-key job, NOT a merge):')
        for r in noaccess:
            print(f"  {r['name']}: {r['pull']}")
        print()

    offline = [r for r in results if r.get('pull', '').startswith('OFFLINE')]
    if offline:
        print('Offline — this host could not reach these remotes (network; clears on '
              'its own, local work is kept):')
        for r in offline:
            print(f"  {r['name']}: {r['pull']}")
        print()

    unrelated = [r for r in results if r.get('pull', '').startswith('UNRELATED HISTORY')]
    if unrelated:
        print('Unrelated history — local checkout shares no ancestor with origin:')
        for r in unrelated:
            print(f"  {r['name']}")
        print()

    held = [r for r in results if r['status'].startswith('SKIP')]
    if held:
        print("Held back — on a non-default branch, needs a human decision:")
        for r in held:
            print(f"  {r['name']}: {r['status']}")
        print()

    verb = 'Committed' if execute else 'Would commit'
    print(f"{verb} {total_c} file(s); skipped {total_s} as junk.")

    # Exit non-zero when a repo is genuinely stuck, so the caller — systemd,
    # cron, a shell pipeline — sees a failure instead of a green run.
    #
    # Printing "PULL CONFLICT — needs a human" to stdout and then returning 0
    # is how 87 pull conflicts accumulated on nightshift across 14 days with
    # nothing escalating (#48). The unit recorded ExecMainStatus=0 on every
    # run while the affected repos stopped converging entirely; the first
    # occurrence in the retained journal was 2026-08-07 and it was noticed on
    # 2026-08-21, by hand, only after two agent fleets had gone blind.
    #
    # Only conflicts fail the run. `held` repos are parked on a non-default
    # branch, which is a deliberate and often long-lived state — failing on it
    # would leave the unit permanently red and train whoever reads it to
    # ignore the signal. (What does fail is unpushed work on ANY branch older
    # than a day -- `stranded` below, SYN-5: that is work at risk, not a
    # parked branch.) That habit is exactly what let a stale-input verifier
    # report four confident wrong failures a day for five days before anyone
    # looked. A check that is always red is not a check.
    forked = [r for r in results if r.get('ledger_fork')]
    if forked:
        print(f"\nFAIL: {len(forked)} repo(s) hold a ledger fork this sweep refused "
              f"to publish (work committed locally, not pushed):")
        for r in forked:
            print(f"  {r['name']}: {'; '.join(r['ledger_fork'])[:200]}")

    deleting = [r for r in results if r.get('deletions')]
    if deleting:
        # Decision Q7: a deletion refusal fails the run (exit 1). A failed
        # Q8 fetch is one too: its range could not be checked, so it is listed
        # here with sha '?' and the fetch error in place of a path.
        print(f"\nFAIL: {len(deleting)} repo(s) would publish deletions of tracked files, "
              f"or their range could not be checked; push refused (work committed "
              f"locally, not pushed). A human decides:")
        for r in deleting:
            print(f"  {r['name']}:")
            for sha, paths in r['deletions']:
                print(f"    {sha}")
                for path in paths:
                    print(f"      - {path}")
    # WORK STUCK ON THIS MACHINE FOR MORE THAN A DAY FAILS THE RUN (SYN-5).
    # Held side branches used to print under "Held back" and exit 0, so the
    # unit stayed green and no alert said which machine and branch the work
    # was stuck on. Named here with the host, and the alert fires.
    import socket
    import time as _time
    host = socket.gethostname().split('.')[0]
    stranded = []
    for repo in repos:
        if repo.name in hold:
            continue
        for branch, n, oldest in stranded_branches(repo):
            stranded.append((repo.name, branch, n, oldest))
    if stranded:
        print(f"\nFAIL: work older than a day is only on {host} (not on any remote):")
        for name, branch, n, oldest in stranded:
            print(f"  {host}: {name} branch {branch}: {n} unpushed commit(s), oldest "
                  f"{_time.strftime('%Y-%m-%d %H:%M', _time.localtime(oldest))}")

    losing = [r for r in results if r.get('incoming_loss') or r.get('shrunk')]
    if losing:
        # A large deletion or a drastic shrink stops the sync (MEM-61).
        print(f"\nFAIL: {len(losing)} repo(s) would lose content in sync; held, files kept here:")
        for r in losing:
            for what in r.get('incoming_loss', []):
                print(f"  {r['name']}: origin {what}")
            for what in r.get('shrunk', []):
                print(f"  {r['name']}: this machine shrank {what}")

    foreign = [r for r in results if r.get('foreign')]
    if foreign:
        # Named, and the run fails so the alert fires. A person moves each commit
        # to a branch and opens its pull request, then resets the default branch
        # to origin; the sweep never publishes them.
        print(f"\nFAIL: {len(foreign)} code repo(s) hold commits on their default branch that "
              f"origin lacks and no sweep made; NOT pushed (these change only by pull request):")
        for r in foreign:
            print(f"  {r['name']} [{r['branch']}]:")
            for line in r['foreign']:
                print(f"    {line}")

    refused = [r for r in results if r.get('hook_refused')]
    if refused:
        # Named, and the run fails so the alert fires: a refused file stays on
        # this machine until a person fixes what the hook objects to (SYN-8).
        print(f"\nFAIL: {len(refused)} repo(s) hold files a pre-commit hook refused "
              f"(everything else landed; these stay here, unchanged):")
        for r in refused:
            print(f"  {r['name']}: {', '.join(r['hook_refused'])}")
    if waiting:
        # Same rule as a refused file (SYN-8): named, and the run fails, until
        # a person settles it -- the rest of each space already went through.
        print(f"\nFAIL: {len(waiting)} repo(s) have a conflict waiting for a person "
              f"(a task names it; the rest of each space went through).")
    if forked or deleting or refused or stranded or losing or foreign or waiting:
        return 1

    if conflicts:
        print(
            f"\nFAIL: {len(conflicts)} repo(s) have pull conflicts and are not "
            f"converging. Resolve the merge in each, then re-run."
        )
        return 1

    if unrelated:
        print(f"\nFAIL: {len(unrelated)} repo(s) share no history with origin.")
        return 1

    # A REPO THIS HOST CANNOT REACH NEVER FAILS THE RUN. Whether it is holding
    # work is a question only a machine that can see the remote can answer, and
    # git_relay.py --check already answers it, from the operator machine,
    # verifying against origin rather than a ref this host cannot refresh.
    # Deciding it here from `@{u}` made the run fail permanently on five repos
    # whose work was already on origin — a check that cannot go green, which is
    # the exact failure this tool exists to prevent elsewhere.
    stuck = [r for r in noaccess if r.get('access_at_risk')]
    if stuck:                      # kept: a future caller may set this honestly
        print(
            f"\nFAIL: {len(stuck)} repo(s) hold unpushed work this host cannot "
            f"send. Grant it access (deploy key / token) — there is nothing to "
            f"merge."
        )
        return 1
    if noaccess:
        print(f"\nNote: {len(noaccess)} repo(s) unreachable from this host but "
              f"holding nothing to send — stale, not stuck. A deploy key fixes "
              f"the staleness; no work is at risk.")

    return 0


if __name__ == '__main__':
    sys.exit(main())
