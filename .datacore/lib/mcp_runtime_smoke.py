#!/usr/bin/env python3
"""Exercise installed MCP Python helpers against disposable signed data."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import subprocess
import sys
import tempfile

LIBRARY = Path(__file__).resolve().parent
sys.path.insert(0, str(LIBRARY))
from ledger.log import EventLog  # noqa: E402


def verify() -> dict[str, str]:
    with tempfile.TemporaryDirectory(prefix='datacore-mcp-runtime-') as directory:
        root = Path(directory).resolve()
        space = root / 'nested/personal'
        (space / '.datacore').mkdir(parents=True)
        marker = space / '.datacore/config.yaml'
        marker.write_text('space: {name: self, type: personal}\n')
        registry = root / '.datacore/keys/registry.yaml'
        log = EventLog(space, 'fixture', sign=True, keys_dir=root / 'private-keys',
                       registry_path=registry)
        log.append('item.create', {'id': 'fixture-task', 'title': 'Synthetic retained task'})

        def invoke(helper: str, *, returncode: int = 0) -> dict:
            result = subprocess.run([sys.executable, '-I', str(LIBRARY / helper),
                                     '--root', str(root)], capture_output=True, text=True, timeout=15)
            assert result.returncode == returncode, 'installed helper failed'
            return json.loads(result.stdout)

        catalog = invoke('space_catalog.py')
        assert catalog == {'version': 1, 'spaces': [
            {'path': 'nested/personal', 'name': 'self', 'type': 'personal', 'marked': True}]}
        first = invoke('ledger_health.py')
        assert first['ok'] is True and first['spaces_verified'] == 1
        # Each observation is a fresh interpreter reading the durable result.
        assert invoke('ledger_health.py') == first
        with log.path.open('rb') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            busy = invoke('ledger_health.py')
            assert busy['ok'] is None and busy['spaces_unverified'] == 1
        assert invoke('ledger_health.py')['ok'] is True
        saved_registry = registry.read_bytes()
        registry.rename(root / 'preserved-registry.yaml')
        assert invoke('ledger_health.py')['ok'] is None
        registry.write_bytes(saved_registry)
        original = log.path.read_bytes()
        log.path.write_bytes(original.replace(b'Synthetic', b'Corrupted'))
        corrupt = invoke('ledger_health.py')
        assert corrupt['ok'] is False and corrupt['spaces_broken'] == 1
        assert 'Corrupted' not in json.dumps(corrupt)
        log.path.write_bytes(original)
        assert invoke('ledger_health.py')['ok'] is True
        marker.write_text('space: [invalid]\n')
        assert invoke('space_catalog.py', returncode=1) == {
            'version': 1, 'error': 'discovery-unverified'}
    return {name: 'PASS' for name in ('canonical_discovery', 'signed_ledger',
            'fresh_process_retrieval', 'busy_writer', 'missing_verification_key',
            'tampered_data', 'preserved_recovery', 'invalid_identity_refusal')}


if __name__ == '__main__':
    print(json.dumps(verify(), sort_keys=True))
