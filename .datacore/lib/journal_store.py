"""Journal updates share the recoverable transaction used by core writers."""
from __future__ import annotations

import errno
import os
from pathlib import Path
import stat
from typing import Callable

from org_transaction import RecoveryRequired, digest, serialized, watch_file, write_org_text


def read_journal(path: Path) -> str | None:
    """Read exact UTF-8 regular-file content without following a final symlink."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ValueError('Journal read cannot follow a source symlink') from None
        raise
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('Journal source must be a regular file')
        return stream.read().decode('utf-8')


@serialized
def update_journal(path: Path, modifier: Callable[[str | None], str]) -> None:
    """Apply a pure modifier under the same lock/recovery protocol as wrap-up.

    Observed unrelated edits can be rebased before enrolling the source in the
    transaction. All cooperating writers must share DATACORE_STATE. Advisory
    locking alone does not constrain other hosts or noncooperating processes.
    """
    path = Path(path)
    for _ in range(3):
        source = read_journal(path)
        result = modifier(source)
        if not isinstance(result, str):
            raise TypeError('Journal modifier must return text')
        if read_journal(path) != source:
            continue
        entry = watch_file(path)
        if entry['current'] != digest(source):
            raise RecoveryRequired('Journal changed before transaction enrollment; source retained')
        write_org_text(path, result)
        return
    raise RuntimeError('Text changed repeatedly during mutation; current source retained')
