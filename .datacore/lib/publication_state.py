"""Retain ambiguous local publication attempts across crashes and retries.

This is a cooperative repository boundary, not an OS sandbox. An unexpected
commit remains available for inspection but cannot become an automatic retry's
accepted base. Removing a retained record requires reconciliation of its refs,
declared paths and expected tree; age or a dead process is not approval.
"""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import stat
import subprocess
import uuid

from file_utils import atomic_write_json, fsync_directory
from git_publication import durable_git_arguments
from worktree_lifecycle import git_environment


def _git(repo, *args):
    try:
        result = subprocess.run(durable_git_arguments(['git', '--literal-pathspecs', *args]), cwd=repo,
                                capture_output=True, timeout=60, env=git_environment())
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError('Publication state is unavailable; retain local work') from None
    if args[:2] == ('symbolic-ref', '-q') and result.returncode == 1:
        return ''  # detached HEAD has no symbolic branch, but still has a commit
    if result.returncode:
        raise RuntimeError('Publication state cannot resolve its repository or refs')
    return result.stdout.decode('utf-8').strip()


def _path(repo):
    common = Path(_git(repo, 'rev-parse', '--path-format=absolute', '--git-common-dir'))
    return common / 'datacore-publication-pending.json'


def require_clear(repo):
    path = _path(repo)
    try:
        path.lstat()
    except FileNotFoundError:
        return
    raise RuntimeError('Unverified publication requires reconciliation; pending record retained in Git storage')


class Reservation:
    def __init__(self, repo, branch, paths):
        self.repo = Path(repo)
        self.path = _path(repo)
        self.verified = False
        self.data = {
            'version': 1, 'token': uuid.uuid4().hex,
            'source_branch': _git(repo, 'symbolic-ref', '-q', 'HEAD'),
            'source_head': _git(repo, 'rev-parse', '--verify', 'HEAD^{commit}'),
            'target_branch': 'refs/heads/' + branch,
            'target_head': _git(repo, 'rev-parse', '--verify', f'refs/heads/{branch}^{{commit}}'),
            'paths': list(paths), 'expected_tree': None,
        }
        self.data['expected_ref'] = 'refs/datacore/publication-captures/' + self.data['token']

    def create(self):
        # Exclusive creation serializes publishers from linked worktrees too.
        # A partial/empty record after interruption fails closed on the next run.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(self.data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        fsync_directory(self.path.parent)

    def expected(self, tree):
        # JSON contains object identities but is not a Git reachability root.
        # Anchor captured data before hooks can replace the only working copy.
        _git(self.repo, 'update-ref', self.data['expected_ref'], tree, '0' * len(tree))
        self.data['expected_tree'] = tree
        atomic_write_json(self.path, self.data)

    def unchanged(self):
        return (_git(self.repo, 'symbolic-ref', '-q', 'HEAD') == self.data['source_branch']
                and _git(self.repo, 'rev-parse', '--verify', 'HEAD^{commit}') == self.data['source_head']
                and _git(self.repo, 'rev-parse', '--verify', self.data['target_branch']) == self.data['target_head'])

    def clear(self):
        metadata = self.path.lstat()
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077):
            raise RuntimeError('Publication record ownership changed; retain it for reconciliation')
        with self.path.open() as stream:
            if json.load(stream) != self.data:
                raise RuntimeError('Publication record changed; retain it for reconciliation')
        if self.data['expected_tree'] is not None:
            if self.verified:
                # The verified commit/base now anchors this exact tree.
                _git(self.repo, 'update-ref', '-d', self.data['expected_ref'], self.data['expected_tree'])
            else:
                # Unchanged refs allow another attempt, but hooks may have
                # edited working files. Preserve the capture and its intent.
                from worktree_lifecycle import allocate_publication_workspace
                retained = allocate_publication_workspace(self.repo) / 'publication-intent.json'
                atomic_write_json(retained, self.data)
        self.path.unlink()
        fsync_directory(self.path.parent)


@contextmanager
def reserve(repo, branch, paths):
    reservation = Reservation(repo, branch, paths)
    reservation.create()
    try:
        # Capture preceded exclusive creation; recheck after ownership exists.
        if not reservation.unchanged():
            raise RuntimeError('Publication refs changed during reservation')
        yield reservation
    finally:
        if reservation.verified or reservation.unchanged():
            reservation.clear()
