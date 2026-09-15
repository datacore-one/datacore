"""Resolve an existing Org source to its own ledger boundary."""
from pathlib import Path


def validate_org_write_path(raw) -> Path:
    """Resolve a write path without allowing a file alias to cross its space.

    An alias of the whole installation is supported. Within an Org tree,
    both the directory and the target must remain in that lexical space.
    Explicit standalone files are supported, but not final-file symlinks.
    """
    path = Path(raw).absolute()
    real = path.resolve(strict=True)
    if not real.is_file() or path.is_symlink():
        raise ValueError('Org write boundary rejects non-files and file symlinks')
    for parent in path.parents:
        if parent.name == 'org':
            space = parent.parent.resolve(strict=True)
            org = parent.resolve(strict=True)
            if org != space / 'org' or not real.is_relative_to(org):
                raise ValueError('Org write crosses its space boundary')
            break
    return real


def ledger_space_for_file(raw) -> Path | None:
    """Return the nearest Org space with a ledger, or refuse the source.

    An arbitrary ancestor's events directory is not authority for this file.
    Whole-installation path aliases are allowed; a file or Org-directory
    symlink crossing the lexical space boundary is not.
    """
    if not isinstance(raw, (str, Path)) or not raw:
        return None
    try:
        path = Path(raw).absolute()
        real = path.resolve(strict=True)
        if not real.is_file():
            return None
        for parent in path.parents:
            if parent.name != 'org':
                continue
            space = parent.parent.resolve(strict=True)
            org = parent.resolve(strict=True)
            if org != space / 'org' or not real.is_relative_to(org):
                return None
            return space if (space / '.datacore/events').is_dir() else None
    except (OSError, ValueError, RuntimeError):
        return None
    return None
