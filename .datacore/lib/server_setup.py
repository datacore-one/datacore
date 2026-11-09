#!/usr/bin/env python3
"""Prepare a minimal CoS + Nightshift Mac server using existing components.

Plan is read-only. Apply prepares identity/configuration but starts no jobs.
Activate installs the reviewed launchd schedules. Provider/chat authentication
and private space Git remotes stay explicit; no personal fleet is copied.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shlex
import sys

import yaml


def plan(root, owner, cos, executor, host, signing=False, briefing_space='0-personal'):
    from ledger_install import validate_actor
    for actor in (owner, cos, executor, host):
        validate_actor(actor)
    if len({owner, cos, executor}) != 3:
        raise ValueError('owner, CoS and executor must have distinct identities')
    root = Path(root).resolve()
    if Path(briefing_space).name != briefing_space or not (root / briefing_space / 'org').is_dir():
        raise ValueError('briefing space must be an installed space with org/')
    py = str(root / '.datacore/venv/bin/python3')
    q = shlex.quote
    common = {'DATACORE_ROOT': str(root), 'DATACORE_PATH': str(root),
              'DATACORE_LEDGER_SIGN': '1' if signing else '0'}
    def job(name, cron, command, actor):
        return {'id': name, 'description': name.replace('-', ' '), 'schedule': cron,
                'command': command, 'enabled': True,
                'environment': {**common, 'DATACORE_ACTOR': actor}}
    jobs = [
        job('server-ledger-sync', '*/15 * * * *', 'bash .datacore/lib/ledger_phase1_cycle.sh', cos),
        job('server-nightshift', '0 2 * * *', 'nightshift run', executor),
        job('server-cos', '0 7 * * *',
            f'cd {q(str(root / ".datacore/modules/chief-of-staff"))} && {q(py)} -m lib.cli run', cos),
    ]
    jobs[-1]['environment']['COS_BRIEFINGS_ROOT'] = str(root / briefing_space / 'notes/briefings')
    return {'version': 1, 'root': str(root), 'owner': owner, 'cos': cos,
            'executor': executor, 'host': host, 'signing': signing,
            'chat': 'optional', 'schedules': jobs}


def prerequisites(root):
    """Check installed entry points and interpreter, never acquire packages on startup."""
    import subprocess
    checks = []
    for rel in ('.datacore/venv/bin/python3', '.datacore/lib/ledger_phase1_cycle.sh',
                '.datacore/modules/nightshift/nightshift', '.datacore/modules/chief-of-staff/lib/cli.py',
                '.datacore/modules/node_modules/@datacore-one/mcp/package.json'):
        checks.append({'check': rel, 'ok': (root / rel).is_file()})
    py = root / '.datacore/venv/bin/python3'
    if py.is_file():
        p = subprocess.run([str(py), '-c', 'import yaml, org_workspace, cryptography'],
                           capture_output=True, timeout=30)
        checks.append({'check': 'Python execution dependencies', 'ok': p.returncode == 0})
    spaces = sorted(p for p in root.glob('[0-9]*-*') if (p / 'org').is_dir())
    checks.append({'check': 'at least one installed private space', 'ok': bool(spaces)})
    for space in spaces:
        for name, valid in (('ledger-phase', {'1'}), ('ledger-edit-protocol', {'1', '2'})):
            marker = space / '.datacore' / name
            checks.append({'check': f'{space.name}/{name}',
                           'ok': marker.is_file() and marker.read_text().strip() in valid,
                           'fix': 'Use ledger_cli init, ingest, then ledger_phase1_flip; see the installation guide.'})
        top = subprocess.run(['git', '-C', str(space), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True, timeout=10)
        remote = subprocess.run(['git', '-C', str(space), 'remote', 'get-url', 'origin'],
                                capture_output=True, text=True, timeout=10)
        checks.append({'check': f'{space.name} independent Git clone with origin',
                       'ok': top.returncode == 0 and Path(top.stdout.strip()).resolve() == space.resolve()
                             and remote.returncode == 0 and bool(remote.stdout.strip())})
        ignored = subprocess.run(['git', '-C', str(space), 'check-ignore', '-q', '--',
                                  '0-inbox/nightshift-install-check.md'], timeout=10, capture_output=True)
        checks.append({'check': f'{space.name} Nightshift reports can sync', 'ok': ignored.returncode == 1,
                       'fix': 'In this PRIVATE space, explicitly allow 0-inbox/nightshift-*.md in .gitignore.'})
    return checks


def apply(doc):
    from file_utils import atomic_write_yaml
    from ledger_install import add_principal, declare_identity
    root = Path(doc['root'])
    missing = [c['check'] for c in prerequisites(root) if not c['ok']]
    if missing:
        raise ValueError('install prerequisites first: ' + ', '.join(missing))
    from actor_identity import principal_of
    for actor, kind in ((doc['owner'], 'human'), (doc['cos'], 'agent'), (doc['executor'], 'agent')):
        principal, entry = principal_of(actor, root / '.datacore/registry/principals.yaml')
        if principal and entry.get('kind') != kind:
            raise ValueError(f'{actor} is already registered as a different principal kind')
    config = root / '.datacore/config/server.local.yaml'
    schedules = root / '.datacore/modules/nightshift/schedules.local.yaml'
    # Re-runs preserve operator edits: never replace a different installation.
    expected = [(config, doc), (schedules, {'schedules': doc['schedules']})]
    for path, content in expected:
        if path.exists() and yaml.safe_load(path.read_text()) != content:
            raise ValueError(f'{path} differs; review it before changing this installation')
    roster = root / '.datacore/registry/infrastructure.yaml'
    if roster.exists():
        existing = yaml.safe_load(roster.read_text()) or {}
        declared = ((existing.get('servers') or {}).get(doc['host']) or {}).get('ledger_actors', [])
        if not {doc['cos'], doc['executor']} <= set(declared):
            raise ValueError('existing roster must declare both service writers on the selected host')
    declare_identity(doc['cos'])
    add_principal(doc['owner'], kind='human')
    for actor in (doc['cos'], doc['executor']):
        add_principal(actor, kind='agent')
        if doc['signing']:
            from ledger.keys import ensure_keypair
            ensure_keypair(actor)
    if not roster.exists():
        atomic_write_yaml(roster, {'roles': {'always_on': doc['host'], 'executor': doc['host']},
            'servers': {doc['host']: {'kind': 'server', 'ledger_actors': [doc['cos'], doc['executor']],
                                     'access': {'actor': doc['cos']}}}})
    for path, content in expected:
        atomic_write_yaml(path, content)
    return [str(path) for path, _ in expected]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('command', choices=['plan', 'apply', 'doctor', 'activate'])
    ap.add_argument('--root', type=Path, default=Path.home() / 'Data')
    ap.add_argument('--owner')
    ap.add_argument('--cos-actor', default='cos')
    ap.add_argument('--executor-actor', default='nightshift')
    ap.add_argument('--host', default='server')
    ap.add_argument('--briefing-space', default='0-personal')
    ap.add_argument('--sign', action='store_true')
    args = ap.parse_args()
    root = args.root.resolve()
    os.environ['DATACORE_ROOT'] = str(root)
    from ledger_install import InstallRefused
    try:
        if args.command == 'doctor':
            checks = prerequisites(root)
            print(json.dumps({'checks': checks, 'prerequisites_ok': all(c['ok'] for c in checks),
                              'remaining': 'Verify private-space remotes, signing trust, provider login and the handoff drill before activation.'}))
            return 0 if all(c['ok'] for c in checks) else 1
        if args.command == 'activate':
            if platform.system() != 'Darwin':
                raise ValueError('this server profile uses macOS launchd')
            from importlib import import_module
            sys.path.insert(0, str(root / '.datacore/modules/nightshift/lib'))
            loader = import_module('scheduler.base').load_schedules
            adapter = import_module('scheduler.launchd_adapter').LaunchdAdapter(str(root))
            path = root / '.datacore/modules/nightshift/schedules.local.yaml'
            if not path.is_file():
                raise ValueError('apply the reviewed server profile first')
            results = adapter.install_all(loader(str(path)))
            print(json.dumps(results))
            return 0 if results and all(results.values()) else 1
        if not args.owner:
            raise ValueError('--owner is required')
        doc = plan(root, args.owner, args.cos_actor, args.executor_actor, args.host,
                   args.sign, args.briefing_space)
        print(json.dumps(doc if args.command == 'plan' else {'written': apply(doc)}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, InstallRefused) as exc:
        print(f'server setup: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
