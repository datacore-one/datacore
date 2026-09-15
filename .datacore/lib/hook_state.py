"""Private, collision-resistant state paths for session hooks."""
import hashlib
import re

from file_utils import private_state_directory


def state_path(kind, session_id='process'):
    if not re.fullmatch(r'[a-z][a-z0-9-]*', kind):
        raise ValueError('invalid hook state kind')
    if not isinstance(session_id, str):
        raise ValueError('invalid session identity')
    root=private_state_directory('hook-state')
    token=hashlib.sha256(session_id.encode('utf-8')).hexdigest()
    path=root/f'{kind}-{token}.json'
    if path.is_symlink():
        raise ValueError('hook state file cannot be a symbolic link')
    return path
