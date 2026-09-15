"""Merge captured publication commits without using a writer's checkout.

Both parents and the expected merge tree are verified before an explicit
remote compare-and-swap. Conflicts, hook changes and failed publication retain
the source, fetched base and private candidate for reconciliation.
"""
from pathlib import Path
import re
import subprocess
import uuid

from git_publication import durable_git_arguments, push_arguments
from worktree_lifecycle import git_environment, publication_hooks, allocate_publication_workspace


class IntegrationError(RuntimeError):
    pass


def _command(repo, args):
    try:
        return subprocess.run(durable_git_arguments(args), cwd=repo, env=git_environment(), capture_output=True,
                              text=True, errors='replace', timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise IntegrationError('Integration command failed or timed out; source retained') from None


def _oid(value):
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', value):
        raise IntegrationError('Integration received an invalid object identity')
    return value


def integrate(repo: Path, source: str, destination_ref: str, *, command=None,
              authorize_source=None) -> None:
    """Publish a verified merge; leave the original branch/index/files alone.

    command is the caller's bounded Git transport adapter, when supplied. It
    does not choose refs, arguments, merge content or acknowledgement rules.
    """
    execute = command or _command

    def git(directory, *args, hooks=None):
        options = ['-c', f'core.hooksPath={hooks}'] if hooks is not None else []
        result = execute(directory, ['git', *options, *args])
        if result.returncode:
            raise IntegrationError(f'Integration git {args[0]} refused; retained work requires inspection')
        return result.stdout.strip()

    source = _oid(source)
    if not isinstance(destination_ref, str) or not destination_ref.startswith('refs/heads/'):
        raise IntegrationError('Integration requires one full branch reference')
    git(repo, 'check-ref-format', destination_ref)
    fetched = git(repo, 'remote', 'get-url', '--all', 'origin').splitlines()
    pushed = git(repo, 'remote', 'get-url', '--push', '--all', 'origin').splitlines()
    if len(fetched) != 1 or fetched != pushed:
        raise IntegrationError('Integration requires one identical fetch/push origin')
    from publication_history import origin_url
    origin = origin_url(repo)
    hooks = publication_hooks(repo)
    namespace = 'refs/datacore/publication/' + uuid.uuid4().hex
    base_ref = namespace + '/base'
    git(repo, 'fetch', '--no-tags', '--no-write-fetch-head', origin, f'{destination_ref}:{base_ref}')
    base = _oid(git(repo, 'rev-parse', '--verify', base_ref + '^{commit}'))
    if authorize_source is not None:
        authorize_source(base, origin)
    ancestry = execute(repo, ['git', 'merge-base', '--is-ancestor', source, base])
    if ancestry.returncode == 0:
        git(repo, 'update-ref', '-d', base_ref, base)
        return
    if ancestry.returncode != 1:
        raise IntegrationError('Integration could not establish ancestry')
    expected = _oid(git(repo, 'merge-tree', '--write-tree', base, source))
    directory = allocate_publication_workspace(repo)
    worktree = directory / 'worktree'
    git(repo, 'worktree', 'add', '--detach', str(worktree), base, hooks=hooks)
    if git(worktree, 'status', '--porcelain', '--untracked-files=all'):
        raise IntegrationError('Checkout hook changed integration workspace; source retained')
    git(worktree, 'merge', '--no-ff', '--no-edit', source, hooks=hooks)
    result = _oid(git(worktree, 'rev-parse', '--verify', 'HEAD^{commit}'))
    if (git(worktree, 'rev-parse', result + '^{tree}') != expected
            or git(worktree, 'show', '-s', '--format=%P', result).split() != [base, source]
            or git(worktree, 'status', '--porcelain', '--untracked-files=all')):
        raise IntegrationError('Integration content or parents changed; candidate retained')
    result_ref = namespace + '/result'
    git(repo, 'update-ref', result_ref, result, '0' * len(result))
    arguments = push_arguments(result, destination_ref, expected=base)
    arguments[-2] = origin
    git(worktree, *arguments, hooks=hooks)
    from worktree_lifecycle import retire_worktree
    retire_worktree(repo, worktree)
    git(repo, 'update-ref', '-d', result_ref, result)
    git(repo, 'update-ref', '-d', base_ref, base)
