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

Sync is bidirectional: --pull also rebases each default-branch repo onto origin
first, so an agent ends the run BOTH visible to the others and on their latest
state. Pushing alone is not enough — Tris's tris-space was 195 commits behind
when this was written, so he was sharing a months-old view of the world.

Usage:
    python3 .datacore/lib/git_fleet_sync.py [data_dir] [--execute] [--pull]
                                            [--hold=repo1,repo2]

Intended to run on a timer on every agent host (nightshift, hermes, plur-claw).
Without that, agents silently re-strand: neither hermes nor plur-claw had any
cron or timer touching git, which is why Tris accumulated 53 uncommitted files
over two months and nobody ever saw his research.
"""

import json
import subprocess
import sys
from pathlib import Path

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


def sync_repo(repo: Path, execute: bool, hold: tuple = (), pull: bool = False) -> dict:
    branch = git(repo, 'branch', '--show-current')
    default = default_branch(repo)

    result = {'name': repo.name, 'branch': branch, 'default': default,
              'skipped': [], 'committed': [], 'status': '', 'pull': ''}

    if repo.name in hold:
        result['status'] = 'SKIP — held back explicitly (--hold)'
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

    # Sync is bidirectional. Pushing agent work out is only half of it — an agent
    # that never pulls drifts onto a stale snapshot of shared knowledge and stops
    # seeing anyone else's. Tris's tris-space was 195 commits behind when this was
    # written, so he was "sharing" a months-old view of the world.
    if pull and execute:
        subprocess.run(['git', 'fetch', '-q', 'origin'], cwd=repo, capture_output=True)
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
        if r.returncode != 0:
            # Never leave a half-applied merge behind for the next run to trip on.
            subprocess.run(['git', 'merge', '--abort'], cwd=repo, capture_output=True)
            result['pull'] = 'PULL CONFLICT — needs a human'
        else:
            result['pull'] = 'pulled'

    porcelain = git_raw(repo, 'status', '--porcelain')
    if not porcelain.strip():
        result['status'] = 'clean' + (f" ({result.get('pull')})" if result.get('pull') else '')
        return result

    tracked = set(git(repo, 'ls-files').splitlines())

    to_add = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        xy = line[:2]
        path = line[3:].strip().strip('"')
        # A "land trapped work" sweep must NEVER propagate a deletion. A tracked
        # file missing on one host is almost always a local defect — an incomplete
        # checkout, a crashed process, an agent that removed it — not work to
        # broadcast. `git add`-ing a ' D' porcelain entry stages the deletion and
        # pushes it to everyone: this is exactly how caa7d58 wiped 110 files from
        # datafund-space on 2026-07-21. Skip deletions; surface them for a human.
        if 'D' in xy:
            result['skipped'].append((path, 'DELETION — not auto-committed (needs a human)'))
            continue
        reason = is_junk(repo, path, tracked)
        if reason:
            result['skipped'].append((path, reason))
        else:
            to_add.append(path)

    if not to_add:
        result['status'] = 'nothing to commit (all junk)'
        return result

    result['committed'] = to_add

    if not execute:
        result['status'] = f'WOULD commit {len(to_add)}, skip {len(result["skipped"])}'
        return result

    for f in to_add:
        subprocess.run(['git', 'add', '--', f], cwd=repo, capture_output=True)

    msg = (
        f"sync: land agent work trapped on this machine ({len(to_add)} files)\n\n"
        "Committed by git_fleet_sync. These changes were made by agents on this\n"
        "host and never committed, so they existed on exactly one disk and were\n"
        "invisible to every other agent.\n\n"
        f"Skipped as junk: {len(result['skipped'])} file(s).\n"
    )
    c = subprocess.run(['git', 'commit', '-m', msg], cwd=repo,
                       capture_output=True, text=True)
    if c.returncode != 0:
        result['status'] = f"COMMIT FAILED: {(c.stderr or '').strip()[:120]}"
        return result

    p = subprocess.run(['git', 'push', 'origin', default], cwd=repo,
                       capture_output=True, text=True)
    if p.returncode != 0:
        result['status'] = f"committed, PUSH FAILED: {(p.stderr or '').strip()[:120]}"
        return result

    result['status'] = f'PUSHED {len(to_add)} file(s) to origin/{default}'
    return result


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


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    execute = '--execute' in sys.argv
    pull = '--pull' in sys.argv
    root = Path(args[0]).expanduser() if args else Path.home() / 'Data'

    hold = ()
    for a in sys.argv[1:]:
        if a.startswith('--hold='):
            hold = tuple(x.strip() for x in a.split('=', 1)[1].split(',') if x.strip())

    if not execute:
        print("DRY RUN — nothing will be committed. Pass --execute to act.\n")

    results = [sync_repo(r, execute, hold, pull) for r in find_repos(root)]

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
    # ignore the signal. That habit is exactly what let a stale-input verifier
    # report four confident wrong failures a day for five days before anyone
    # looked. A check that is always red is not a check.
    if conflicts:
        print(
            f"\nFAIL: {len(conflicts)} repo(s) have pull conflicts and are not "
            f"converging. Resolve the merge in each, then re-run."
        )
        return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
