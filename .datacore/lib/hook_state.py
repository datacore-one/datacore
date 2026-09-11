"""Private, collision-resistant state paths for session hooks."""
import hashlib
import os
from pathlib import Path
import re


def state_path(kind, session_id='process'):
    if not re.fullmatch(r'[a-z][a-z0-9-]*', kind):
        raise ValueError('invalid hook state kind')
    if not isinstance(session_id, str):
        raise ValueError('invalid session identity')
    root=Path(os.environ.get('DATACORE_STATE', Path.home()/'.datacore/state'))/'hook-state'
    if root.is_symlink():
        raise ValueError('hook state directory cannot be a symbolic link')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    info=root.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('hook state directory must be private to its owner')
    token=hashlib.sha256(session_id.encode('utf-8')).hexdigest()
    path=root/f'{kind}-{token}.json'
    if path.is_symlink():
        raise ValueError('hook state file cannot be a symbolic link')
    return path
