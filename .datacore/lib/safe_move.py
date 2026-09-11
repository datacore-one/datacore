"""Atomic file moves that cannot replace an existing destination.

Cross-filesystem moves deliberately fail: copy/delete is not an atomic move.
Darwin and Linux expose a no-replace rename primitive; unsupported hosts fail
before removing source data. Callers synchronize related metadata separately.
"""
import ctypes
import errno
import os
from pathlib import Path
import sys

from file_utils import fsync_directory


def rename_noreplace(source, destination):
    source, destination = Path(source), Path(destination)
    if source.is_symlink():
        raise ValueError('cannot archive a symbolic link')
    if not source.is_file():
        raise ValueError('archive source must be a regular file')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        os.rename(source, destination)  # Windows rejects existing destinations.
    else:
        library = ctypes.CDLL(None, use_errno=True)
        src, dst = os.fsencode(source), os.fsencode(destination)
        if sys.platform == 'darwin' and hasattr(library, 'renamex_np'):
            rename = library.renamex_np
            rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(src, dst, 0x4)  # RENAME_EXCL, SDK sys/stdio.h
        elif sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
            rename = library.renameat2
            rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(-100, src, -100, dst, 1)  # AT_FDCWD, RENAME_NOREPLACE
        else:
            raise OSError(errno.ENOTSUP, 'atomic no-replace rename is unavailable')
        if result:
            number = ctypes.get_errno()
            raise OSError(number, os.strerror(number), str(destination))
    fsync_directory(destination.parent)
    if source.parent != destination.parent:
        fsync_directory(source.parent)
