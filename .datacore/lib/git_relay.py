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
        f'for d in {root}/[0-9]-*/; do '
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
            probe = _ssh(host, f'cd {root}/{repo} && '
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
    """Ledger files in `repo` holding two events with one (actor, seq).

    A merge must never create these. `(actor, seq)` identifies exactly one
    event forever (DIP-0046), so two events sharing it is a fork — and a
    relay that pushes one propagates it to every machine. This tool did
    exactly that on 2026-08-30: merging a host's stale genesis.jsonl put 9
    forked events on origin, and v2-verify (which only runs on winston,
    twice a day) was the only thing that noticed.

    So the guard lives HERE, at the moment of creation, not only in a
    checker somewhere else on a schedule.
    """
    import collections
    bad = []
    events = Path(repo) / '.datacore' / 'events'
    if not events.is_dir():
        return bad
    for f in sorted(events.glob('*.jsonl')):
        seen = collections.defaultdict(set)
        try:
            for line in f.read_text(errors='replace').splitlines():
                if not line.strip():
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                key = (e.get('actor'), e.get('seq'))
                if key[1] is None:
                    continue
                seen[key].add(e.get('hash') or line)
        except OSError:
            continue
        forked = [k for k, v in seen.items() if len(v) > 1]
        if forked:
            bad.append(f"{f.name}: {len(forked)} forked (actor,seq) "
                       f"e.g. {forked[0]}")
    return bad


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
    host_remote = _ssh(host, f'git -C {root}/{repo} remote get-url origin'
                       ).stdout.strip()
    local = _local_clone_for(data_dir, host_remote)
    if local is None:
        return (f"{repo}: no local clone of {host_remote or 'its remote'} on "
                f"this machine — cannot relay")

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

        merge = _run(['git', '-C', str(local), 'merge', '--no-edit',
                      f'{remote_name}/{branch}'], timeout=300)
        if merge.returncode != 0:
            resolver = data_dir / '.datacore' / 'lib' / 'resolve_ledger_conflicts.py'
            if resolver.is_file():
                _run(['python3', str(resolver), repo], cwd=str(data_dir),
                     timeout=300)
            if _run(['git', '-C', str(local), 'ls-files', '-u']).stdout.strip():
                _run(['git', '-C', str(local), 'merge', '--abort'])
                return (f"{repo}: merge from {host} conflicts beyond the "
                        f"resolver — needs a human")

        # NEVER PUSH A FORK. The merge above can only have combined two
        # per-writer logs, and if it produced two events sharing an
        # (actor,seq) then pushing would hand that fork to every machine.
        # Abort and leave the merge for a human — the host's work is still
        # safe where it was.
        forks = ledger_forks(local)
        if forks:
            _run(['git', '-C', str(local), 'reset', '--hard', 'HEAD~1'])
            return (f"{repo}: REFUSED — merging {host} would fork the ledger "
                    f"({'; '.join(forks)[:160]}). Merge reverted; nothing "
                    f"pushed. Resolve by hand.")

        # Converge with origin before pushing. The relaying machine is not
        # necessarily up to date itself, and a rejected push would leave the
        # host's work sitting on THIS machine instead — trapped one hop
        # further along, which is no improvement.
        pull = _run(['git', '-C', str(local), 'pull', '--no-rebase', '-q',
                     'origin', branch], timeout=300)
        if pull.returncode != 0:
            resolver = data_dir / '.datacore' / 'lib' / 'resolve_ledger_conflicts.py'
            if resolver.is_file():
                _run(['python3', str(resolver), repo], cwd=str(data_dir),
                     timeout=300)
            if _run(['git', '-C', str(local), 'ls-files', '-u']).stdout.strip():
                _run(['git', '-C', str(local), 'merge', '--abort'])
                return (f"{repo}: host work merged locally, but converging "
                        f"with origin conflicts beyond the resolver — "
                        f"needs a human (nothing lost)")

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
        root = DATA_ROOT
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
