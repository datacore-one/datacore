"""Durable, content-preserving updates for local credential stores."""
import hashlib
import json
from pathlib import Path

from file_utils import atomic_write_text, file_lock


def adopt_oauth_token(path, token):
    path = Path(path)
    with file_lock(path):
        if path.is_symlink():
            raise ValueError('credential store must not be a symbolic link')
        try:
            before = path.read_bytes()
        except FileNotFoundError:
            before = None
        document = json.loads(before) if before is not None else {}
        if not isinstance(document, dict) or not isinstance(document.get('claudeAiOauth', {}), dict):
            raise ValueError('invalid credential store; existing bytes preserved')
        old = document.get('claudeAiOauth', {}).get('accessToken', '')
        if before is not None:
            # Preserve the original bytes, including the outgoing refresh
            # chain and unknown fields, before deriving the replacement.
            directory = path.with_name(path.name + '.backups')
            if directory.is_symlink():
                raise ValueError("credential backup directory cannot be a symbolic link")
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            backup = directory / (hashlib.sha256(before).hexdigest() + '.json')
            if backup.is_symlink():
                raise ValueError('credential backup cannot be a symbolic link')
            if backup.exists():
                if backup.read_bytes() != before:
                    raise ValueError('credential backup conflict')
            else:
                atomic_write_text(backup, before.decode('utf-8'))
            previous = path.with_suffix(path.suffix + '.prev')
            if previous.is_symlink():
                raise ValueError('previous credential backup cannot be a symbolic link')
            atomic_write_text(previous, before.decode('utf-8'))
        block = document.setdefault('claudeAiOauth', {})
        block['accessToken'] = token
        block.pop('refreshToken', None)
        block.pop('expiresAt', None)
        block.setdefault('scopes', ['user:inference', 'user:profile'])
        atomic_write_text(path, json.dumps(document, allow_nan=False, ensure_ascii=False) + '\n')
        return old


def add_credential(secrets_dir, env_file, entry, value):
    """Publish an env entry and its index row together, preserving old bytes."""
    import re
    import shlex
    import yaml
    from org_transaction import serialized, watch_file, write_org_text
    root = Path(secrets_dir).resolve()
    env_file = Path(env_file)
    index = root / 'credential-index.yaml'
    if env_file.is_symlink() or index.is_symlink() or not env_file.resolve().is_relative_to(root):
        raise ValueError('credential path escapes the secrets repository')
    env_file = env_file.resolve()
    var_name, cred_id = entry.get('var_name'), entry.get('id')
    if not isinstance(var_name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', var_name):
        raise ValueError('invalid credential variable name')
    if not isinstance(cred_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}', cred_id):
        raise ValueError('invalid credential identifier')
    if not isinstance(value, str) or not value or any(c in value for c in '\r\n\0'):
        raise ValueError('credential value must be a nonempty single line')

    @serialized
    def publish():
        watch_file(index); watch_file(env_file)
        document = yaml.safe_load(index.read_bytes())
        if not isinstance(document, dict) or not isinstance(document.get('credentials'), list):
            raise ValueError('invalid credential index; existing data preserved')
        if any(not isinstance(row, dict) for row in document['credentials']):
            raise ValueError('invalid credential index row')
        if any(row.get('id') == cred_id or row.get('var_name') == var_name for row in document['credentials']):
            raise ValueError('credential identifier or variable already indexed; use a rotation/update operation')
        before = env_file.read_bytes().decode('utf-8')
        if re.search(r'^\s*(?:export\s+)?' + re.escape(var_name) + r'\s*=', before, re.M):
            raise ValueError('credential variable already exists in the target store')
        document['credentials'].append(entry)
        write_org_text(env_file, before + '\n' + var_name + '=' + shlex.quote(value) + '\n')
        write_org_text(index, yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    publish()
    return env_file, index
