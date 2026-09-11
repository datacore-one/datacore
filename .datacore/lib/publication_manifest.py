"""Publish only unchanged outputs recorded by one run from a clean checkout."""
import hashlib
from pathlib import Path
import subprocess

from knowledge_commit import GitError, commit_to_branch, default_branch


class PublicationManifest:
    def __init__(self, repo):
        self.repo = Path(repo).resolve()
        self.paths = {}
        self.head = self._git('rev-parse', 'HEAD')
        self.initial = self._git('status', '--porcelain', '--untracked-files=all')

    def _git(self, *args):
        result = subprocess.run(['git', '-C', str(self.repo), *args], capture_output=True, timeout=30)
        return result.stdout if result.returncode == 0 else None

    def record(self, path, content):
        path = Path(path).absolute()
        raw = content.encode('utf-8') if isinstance(content, str) else content
        self.paths[path] = hashlib.sha256(raw).digest()

    def publish(self, message, *, push=True):
        if not self.paths:
            return ''
        if self.head is None or self.initial != b'' or self._git('rev-parse', 'HEAD') != self.head:
            raise GitError('publication requires the original clean checkout; outputs retained locally')
        paths = []
        for path, expected in self.paths.items():
            if path.is_symlink() or not path.resolve().is_relative_to(self.repo):
                raise GitError('output lies outside the publication repository')
            if hashlib.sha256(path.read_bytes()).digest() != expected:
                raise GitError('an output changed after this run wrote it; retain both versions for review')
            paths.append(path.relative_to(self.repo).as_posix())
        return commit_to_branch(self.repo, default_branch(self.repo), paths, message, push=push)
