"""Explicit publication scope, independent of Git's broad push defaults."""
import re


def durable_git_arguments(args: list[str]) -> list[str]:
    """Flush publication objects and refs instead of inheriting unsafe defaults.

    Git's defaults may omit loose-object durability and use writeout-only on
    macOS. Publication/recovery writers require actual fsync before relying on
    captured objects. These command-scoped options do not alter user config.
    """
    if not args or args[0] != 'git':
        raise ValueError('Durability options require a Git invocation')
    return ['git', '-c', 'core.fsync=committed,reference', '-c', 'core.fsyncMethod=fsync', *args[1:]]


def _oid(value: str) -> str:
    if (not isinstance(value, str) or not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value)
            or set(value) == {'0'}):
        raise ValueError('Publication requires a complete immutable object ID')
    return value


def push_arguments(commit: str, destination: str, *, expected: str | None = None) -> list[str]:
    """Publish exactly one commit/ref; an optional lease is always explicit.

    A nonempty expected value is only appropriate after the caller has verified
    that the candidate preserves that base. An empty value requests creation
    only. No implicit tracking-ref lease or destructive force option is exposed.
    """
    _oid(commit)
    if (not isinstance(destination, str) or not destination.startswith('refs/heads/')
            or destination.endswith(('/', '.')) or '..' in destination or '@{' in destination
            or any(ord(c) <= 32 or ord(c) == 127 or c in '~^:?*[\\' for c in destination)
            or any(not part or part.startswith('.') or part.endswith('.lock')
                   for part in destination.split('/'))):
        raise ValueError('Publication requires one complete branch ref')
    # Check referenced submodule commits without implicitly publishing another
    # repository, or acknowledging a parent whose dependencies are unavailable.
    args = ['push', '--no-follow-tags', '--recurse-submodules=check']
    if expected is not None:
        if not isinstance(expected, str):
            raise ValueError('Publication lease must be an explicit object ID or empty string')
        if expected:
            _oid(expected)
        args.append(f'--force-with-lease={destination}:{expected}')
    return [*args, 'origin', f'{commit}:{destination}']
