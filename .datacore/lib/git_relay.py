#!/usr/bin/env python3
"""Land work trapped on a host that cannot reach its own remote.

WHY THIS EXISTS. Access is not uniform across the fleet. winston's key is not
authorised for datafund/datafund-space or fairDataSociety/fds-space, so on
2026-08-30 it held 22 commits of real work — a header rollout, weekday
repairs, journal entries — that it had committed and could never push. The
work was not lost, but it was invisible to every other machine, which is the
same thing from anyone else's point of view.

The operator machine can reach BOTH the host (over SSH) and the remote (over
GitHub). So it relays: fetch the host's branch directly from its filesystem,
converge it locally (MERGE, NEVER REBASE — DIP-0046), push onward, and then
correct the host's remote-tracking ref so it stops believing it is diverged.

This needs no new credential. It is the mechanism that makes an access gap a
DELAY rather than a trap: the gap still wants fixing with a deploy key, but
work stops accumulating behind it in the meantime.

    git_relay.py --check                      # what is trapped, everywhere
    git_relay.py --host winston               # relay every trapped repo there
    git_relay.py --host winston --repos 3-fds
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

#: SSH aliases to inspect. The Data root is resolved on the host itself
#: (`$HOME/Data`) rather than hardcoded here — the path is the remote user's
#: business, and writing it down would bake one machine's layout into a tool
#: that runs against four.
HOSTS = ('winston', 'nightshift', 'plur-claw', 'hermes')
DATA_ROOT = '$HOME/Data'


def _run(cmd, cwd=None, timeout=180):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          timeout=timeout)


def _ssh(host, script, timeout=120):
    return _run(['ssh', '-o', 'ConnectTimeout=15', '-o', 'BatchMode=yes',
                 host, script], timeout=timeout)


def trapped_repos(host: str, root: str, data_dir: Path) -> list[dict]:
    """Repos on `host` whose HEAD is NOT on the remote.

    TRAPPED MEANS "THE WORK IS NOT ON THE REMOTE" — nothing else. An earlier
    version asked the host how far ahead of `@{u}` it was, but a host that
    cannot reach its remote can never update that ref, so it kept reporting
    166 commits as trapped after every one of them had been relayed and
    verified on origin. A check that cannot go green is not a check.

    So ask the machine that CAN see the remote: fetch origin here, and test
    whether the host's HEAD is an ancestor of it.
    """
    script = (
        f'for d in {shlex.quote(root)}/[0-9]-*/; do '
        f'  [ -d "$d/.git" ] || continue; '
        f'  echo "$(basename $d) $(git -C "$d" rev-parse HEAD 2>/dev/null) '
        f'$(git -C "$d" remote get-url origin 2>/dev/null) '
        f'$(git -C "$d" branch --show-current 2>/dev/null)"; '
        f'done')
    r = _ssh(host, script)
    out = []
    for line in (r.stdout or '').splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        repo, sha, url = parts[0], parts[1], parts[2]
        branch = parts[3] if len(parts) > 3 else 'main'
        local = _local_clone_for(data_dir, url)
        if local is None:
            # No local clone of that remote to verify against — so ask the
            # host whether it can push at all. A host that CAN push is not
            # trapped whatever it is holding, and calling it trapped is the
            # same "cannot go green" failure this check was rewritten to
            # avoid. Only a host that is BOTH ahead and unable to push has
            # work stuck behind an access gap.
            probe = _ssh(host, f'cd {shlex.quote(root + "/" + repo)} && '
                               f'n=$(git rev-list --count @{{u}}..HEAD 2>/dev/null || echo 0) && '
                               f'if git push --dry-run origin HEAD >/dev/null 2>&1; '
                               f'then echo "CANPUSH $n"; else echo "NOPUSH $n"; fi')
            verdict = (probe.stdout or '').split()
            if len(verdict) == 2 and verdict[0] == 'NOPUSH' and verdict[1] != '0':
                out.append({'repo': repo, 'commits': int(verdict[1]),
                            'why': f'cannot push, and no local clone of {url} '
                                   f'on this machine to relay through'})
            continue
        _run(['git', '-C', str(local), 'fetch', '-q', 'origin'], timeout=180)
        on_origin = _run(['git', '-C', str(local), 'merge-base',
                          '--is-ancestor', sha, f'origin/{branch}'])
        if on_origin.returncode == 0:
            continue                       # work is on the remote: not trapped
        # Count what origin has not seen. `sha` may be unknown locally, in
        # which case the count is unavailable but the verdict still stands.
        cnt = _run(['git', '-C', str(local), 'rev-list', '--count',
                    f'origin/{branch}..{sha}']).stdout.strip()
        out.append({'repo': repo, 'commits': int(cnt) if cnt.isdigit() else -1,
                    'why': 'HEAD is not on the remote'})
    return out


def ledger_forks(repo: Path) -> list[str]:
    """Ledger files in `repo` whose chain, signature or rewind check fails.

    A merge must never create these. `(actor, seq)` identifies exactly one
    event forever (DIP-0046), so two events sharing it is a fork — and a
    relay that pushes one propagates it to every machine. This tool did
    exactly that on 2026-08-30: merging a host's stale genesis.jsonl put 9
    forked events on origin, and v2-verify (which only runs on winston,
    twice a day) was the only thing that noticed.

    So the guard lives HERE, at the moment of creation, not only in a
    checker somewhere else on a schedule.
    """
    from ledger.verify import verify_chain, check_not_rewound
    bad = []
    events = Path(repo) / '.datacore' / 'events'
    if not events.is_dir():
        return bad
    for f in sorted(events.glob('*.jsonl')):
        try:
            if f.is_symlink():
                raise ValueError('symbolic ledger path')
            errors = verify_chain(f) + check_not_rewound(f)
            if errors:
                bad.append(f'{f.name}: ledger integrity verification failed')
        except (OSError, ValueError, TypeError, AttributeError, KeyError):
            bad.append(f'{f.name}: ledger integrity could not be established')
    return bad


def publication_forks(repo: Path, commit: str, ref: str) -> list[str]:
    """Would making `commit` the new tip of `ref` fork or rewind a ledger log?

    NEVER PUSH A FORK is a property of what a push PUBLISHES, so this reads the
    commit's tree, not the working tree, and compares it with `ref` (the
    remote-tracking ref the push will advance) using fork.py's own predicate:
    an `(actor, seq)` on both sides with different hashes is a fork. Two more
    ways a push can break every other machine's copy are named too:

      rewind   an event `ref` holds is missing from the commit -- a truncated
               or deleted log, which a fork check ignores ("one side ahead")
      chain    the commit's copy fails verify_chain where `ref`'s copy did not

    Why the chain check alone (`ledger_forks`) is not enough: a three-way merge
    takes one side's copy WHOLE when the other side never touched the file. A
    host that rewound its log and re-appended arrives as a perfectly valid
    chain -- it is only a fork relative to origin, which no single copy can
    see (fork.py's module docstring). GitFleet.lean `merge_rewrite_is_not_union`.

    [] means safe to publish. A missing `ref` compares nothing (a first push).
    """
    import tempfile
    from ledger import fork
    from ledger.verify import verify_chain

    repo = Path(repo)
    prefix = '.datacore/events/'

    def names(rev: str) -> set[str] | None:
        rc, out = fork._git(repo, 'ls-tree', '-r', '--name-only', rev, '--', prefix)
        if rc:
            return None
        return {n for n in out.splitlines()
                if n.startswith(prefix) and n.endswith('.jsonl') and '/' not in n[len(prefix):]}

    def blob(rev: str, rel: str) -> str | None:
        rc, out = fork._git(repo, 'show', f'{rev}:{rel}')
        return out if rc == 0 else None

    def chain_ok(rel: str, text: str) -> bool:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / Path(rel).name
            path.write_text(text)
            return not verify_chain(path)

    ours_names = names(commit)
    if ours_names is None:
        return ['ledger logs of the publication commit could not be listed']
    rc, _ = fork._git(repo, 'rev-parse', '--verify', '-q', f'{ref}^{{commit}}')
    theirs_names = (names(ref) or set()) if rc == 0 else set()

    bad = []
    for rel in sorted(ours_names | theirs_names):
        name = rel[len(prefix):]
        ours_text = blob(commit, rel) if rel in ours_names else ''
        theirs_text = blob(ref, rel) if rel in theirs_names else None
        if ours_text is None:
            bad.append(f'{name}: publication copy unreadable')
            continue
        if ours_text == theirs_text:
            continue                       # nothing this push would change
        ours = fork._index(ours_text)
        if theirs_text is not None:
            theirs = fork._index(theirs_text)
            collisions = [k for k in ours.keys() & theirs.keys() if ours[k] != theirs[k]]
            lost = theirs.keys() - ours.keys()
            if collisions:
                bad.append(f'{name}: {len(collisions)} (actor, seq) differ from {ref} — a fork')
            if lost:
                bad.append(f'{name}: {len(lost)} event(s) on {ref} missing — a rewind')
        if ours_text and not chain_ok(rel, ours_text) and (
                theirs_text is None or chain_ok(rel, theirs_text)):
            bad.append(f'{name}: chain fails verification')
    return bad


def _park(repo: Path, host: str, branch: str, pre: str) -> str:
    """Take a refused merge OFF the branch, keeping it as evidence.

    Returning "REFUSED" while the forked merge commit stays on the branch only
    moves the push to the next pusher: git_fleet_sync publishes HEAD, and
    HEAD is the merge (replayed: tests/test_git_fleet_formal.py). The working
    tree was verified clean before the merge, so resetting to `pre` loses
    nothing; the merge stays reachable under refs/relay-refused/.
    """
    ref = f'refs/relay-refused/{host}/{branch}'
    _run(['git', '-C', str(repo), 'update-ref', ref, 'HEAD'])
    reset = _run(['git', '-C', str(repo), 'reset', '-q', '--hard', pre])
    if reset.returncode != 0:
        return f'branch could NOT be restored to {pre[:10]} — do not push; evidence at {ref}'
    return f'branch restored to {pre[:10]}; refused merge kept at {ref}'


def _normalise_remote(url: str) -> str:
    """github.com/org/name from any of the URL forms git accepts."""
    u = (url or '').strip().removesuffix('.git')
    for prefix in ('git@github.com:', 'https://github.com/',
                   'ssh://git@github.com/'):
        if u.startswith(prefix):
            return u[len(prefix):].lower()
    return u.lower()


def _local_clone_for(data_dir: Path, host_remote: str) -> Path | None:
    """The local clone of the SAME remote, whatever this machine calls it.

    Agents name their spaces for themselves: Mr Data's `2-plur-space` and the
    operator's `5-plur` are both clones of plur-ai/plur-space. Matching on
    directory name reported "no local clone" for a repo sitting right there,
    and 8 commits stayed trapped over a naming difference.
    """
    target = _normalise_remote(host_remote)
    if not target:
        return None
    for candidate in sorted(data_dir.glob('[0-9]-*')):
        if not (candidate / '.git').is_dir():
            continue
        url = _run(['git', '-C', str(candidate), 'remote', 'get-url', 'origin']
                   ).stdout.strip()
        if _normalise_remote(url) == target:
            return candidate
    return None


def relay(host: str, root: str, repo: str, data_dir: Path,
          dry_run: bool = False) -> str:
    """Fetch `repo` from `host`, converge locally, push onward."""
    host_remote = _ssh(host, f'git -C {shlex.quote(root + "/" + repo)} remote get-url origin'
                       ).stdout.strip()
    local = _local_clone_for(data_dir, host_remote)
    if local is None:
        return (f"{repo}: no local clone of {host_remote or 'its remote'} on "
                f"this machine — cannot relay")

    clean = _run(['git', '-C', str(local), 'status', '--porcelain', '--untracked-files=all'])
    if clean.returncode or clean.stdout.strip():
        return f'{repo}: REFUSED — preserve existing working/index changes before relay'

    remote_name = f'relay-{host}'
    remote_url = f'{host}:{root}/{repo}'
    _run(['git', '-C', str(local), 'remote', 'remove', remote_name])
    add = _run(['git', '-C', str(local), 'remote', 'add', remote_name, remote_url])
    if add.returncode != 0:
        return f"{repo}: could not add relay remote: {add.stderr.strip()[:120]}"

    try:
        fetch = _run(['git', '-C', str(local), 'fetch', '-q', remote_name],
                     timeout=300)
        if fetch.returncode != 0:
            return f"{repo}: fetch from {host} failed: {fetch.stderr.strip()[:160]}"

        branch = _run(['git', '-C', str(local), 'branch', '--show-current']
                      ).stdout.strip() or 'main'
        ahead = _run(['git', '-C', str(local), 'rev-list', '--count',
                      f'HEAD..{remote_name}/{branch}']).stdout.strip() or '0'
        if ahead == '0':
            return f"{repo}: nothing on {host} that this machine lacks"
        if dry_run:
            return f"{repo}: would relay {ahead} commit(s) from {host}"

        pre = _run(['git', '-C', str(local), 'rev-parse', '--verify', 'HEAD']
                   ).stdout.strip()
        if not pre:
            return f"{repo}: REFUSED — local HEAD unreadable; nothing merged"
        merge = _run(['git', '-C', str(local), 'merge', '--no-edit',
                      f'{remote_name}/{branch}'], timeout=300)
        if merge.returncode != 0:
            return (f"{repo}: merge from {host} failed; conflict/index evidence "
                    f"retained for review; nothing pushed")

        # NEVER PUSH A FORK. A merge is a union only when each side merely
        # APPENDED to each log; a side that rewrote a log is taken whole by a
        # clean three-way merge. So check the chains AND compare with origin
        # (fork.py's predicate), and on refusal take the merge off the branch:
        # a refusal that leaves it there is published by the next pusher.
        # The host's work is still safe where it was.
        forks = ledger_forks(local) + publication_forks(local, 'HEAD', f'origin/{branch}')
        if forks:
            return (f"{repo}: REFUSED — merging {host} would fork the ledger "
                    f"({'; '.join(forks)[:160]}); nothing pushed; "
                    f"{_park(local, host, branch, pre)}. Resolve by hand.")

        # Converge with origin before pushing. The relaying machine is not
        # necessarily up to date itself, and a rejected push would leave the
        # host's work sitting on THIS machine instead — trapped one hop
        # further along, which is no improvement.
        pull = _run(['git', '-C', str(local), 'pull', '--no-rebase', '-q',
                     'origin', branch], timeout=300)
        if pull.returncode != 0:
            return (f"{repo}: origin convergence failed; local commits and "
                    f"conflict/index evidence retained; nothing pushed")

        forks = ledger_forks(local) + publication_forks(local, 'HEAD', f'origin/{branch}')
        if forks:
            return (f'{repo}: REFUSED — origin convergence failed ledger integrity '
                    f"({'; '.join(forks)[:160]}); nothing pushed; "
                    f'{_park(local, host, branch, pre)}')

        push = _run(['git', '-C', str(local), 'push', 'origin', branch],
                    timeout=300)
        if push.returncode != 0:
            detail = (push.stderr or push.stdout).strip().splitlines()
            tail = ' | '.join(l.strip() for l in detail[-3:] if l.strip())
            return f"{repo}: relayed locally but push failed: {tail[:200]}"

        # Correct the host's remote-tracking ref so it stops reporting a
        # divergence that no longer exists. PUSH rather than update-ref: the
        # host cannot fetch, so it does not have the merge commit, and
        # `update-ref` fails with "nonexistent object". Pushing delivers the
        # objects and moves the pointer in one step. The target is a
        # remote-tracking ref, never the checked-out branch, so the host's
        # working tree is untouched.
        _run(['git', '-C', str(local), 'push',
              f'{host}:{root}/{repo}',
              f'HEAD:refs/remotes/origin/{branch}'], timeout=300)
        return f"{repo}: RELAYED {ahead} commit(s) from {host} -> origin"
    finally:
        _run(['git', '-C', str(local), 'remote', 'remove', remote_name])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--host', choices=HOSTS)
    ap.add_argument('--repos', nargs='*')
    ap.add_argument('--check', action='store_true',
                    help='report trapped work everywhere, change nothing')
    ap.add_argument('--forks', action='store_true',
                    help='check THIS machine for forked ledger logs '
                         '(two events sharing one actor+seq) and exit')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--data-dir', default=str(Path.home() / 'Data'))
    a = ap.parse_args()
    data_dir = Path(a.data_dir)

    # Fork check runs locally and needs no SSH, so every machine can run it
    # rather than waiting on winston's twice-daily v2-verify.
    if a.forks:
        total = 0
        for space in sorted(data_dir.glob('[0-9]-*')):
            found = ledger_forks(space)
            for line in found:
                print(f"{space.name}/{line}")
            total += len(found)
        print(f"\n{total} ledger file(s) with forked (actor,seq)")
        return 1 if total else 0

    hosts = [a.host] if a.host else list(HOSTS)
    total = 0
    for host in hosts:
        # RESOLVE $HOME ON THE HOST, once. DATA_ROOT is written with `$HOME` so
        # it is not a personal path in a tracked file, but git does not put a
        # fetch URL through a shell: `git fetch host:$HOME/Data/2-datacore`
        # arrives at the far end as the literal four characters `$HOME`, and
        # every relay failed with "'$HOME/Data/2-datacore' does not appear to
        # be a git repository". --check never noticed because it goes over
        # ssh, which DOES expand it.
        root = _run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                     host, f'echo {DATA_ROOT}'], timeout=60).stdout.strip()
        if not root:
            print(f"{host}: could not resolve {DATA_ROOT} (unreachable?)")
            continue
        try:
            found = trapped_repos(host, root, data_dir)
        except subprocess.TimeoutExpired:
            print(f"{host}: unreachable (timeout)")
            continue
        if not found:
            print(f"{host}: nothing trapped")
            continue
        for item in found:
            if a.repos and item['repo'] not in a.repos:
                continue
            total += max(item['commits'], 1)
            if a.check:
                n = item['commits']
                count = f"{n} commit(s)" if n >= 0 else "work"
                print(f"{host}/{item['repo']}: {count} NOT on the remote "
                      f"— {item.get('why', '')}")
            else:
                print(f"  {relay(host, root, item['repo'], data_dir, a.dry_run)}")

    if a.check:
        print(f"\n{total} commit(s) trapped across the fleet")
    return 1 if (a.check and total) else 0


if __name__ == '__main__':
    sys.exit(main())
