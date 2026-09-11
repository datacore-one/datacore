"""Preserve complete notes when moving them to an archive."""
import hashlib
from pathlib import Path

from org_transaction import move_file, serialized, watch_file


@serialized
def archive_file(source, destination, *, root, expected_hash=None):
    root = Path(root).resolve()
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or destination.is_symlink():
        raise ValueError('archive paths cannot be symbolic links')
    source, destination = source.resolve(), destination.resolve()
    if not source.is_relative_to(root) or not destination.is_relative_to(root):
        raise ValueError('archive path is outside the selected root')
    watch_file(source)
    content = source.read_bytes().decode("utf-8") if source.exists() else None
    if content is None:
        raise FileNotFoundError(source)
    digest = hashlib.sha256(content.encode()).hexdigest()
    if expected_hash is not None and digest != expected_hash:
        raise ValueError('source changed after classification; rescan before archiving')
    stem = destination.stem.encode()[:96].decode(errors='ignore')
    for number in range(100):
        candidate = destination if number == 0 else destination.with_name(
            f'{stem}--{digest}-{number}{destination.suffix[:12]}')
        if candidate.is_symlink():
            raise ValueError('archive destination is a symbolic link')
        if candidate.exists():
            continue
        move_file(source, candidate)
        return candidate
    raise FileExistsError('archive collision limit reached; source preserved')
