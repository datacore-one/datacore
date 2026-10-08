"""Disposable replicas: actual MCP handler -> Git -> worker -> review -> clients.

The model's work is a deterministic artifact; this does not claim to exercise
the Cursor GUI, Windows kernel, provider authentication or launchd activation.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ledger.fold import fold
from ledger.log import EventLog, read_events
from ledger.verify import verify_space
from ledger_project_org import project_space
from ledger_transport import converge

CORE = Path(__file__).resolve().parents[1]
ROOT = CORE.parents[1]
NS = ROOT / '.datacore/modules/nightshift/lib'


def git(root, *args):
    p = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return p.stdout.strip()


def test_two_local_clients_and_server_share_auditable_work(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_LIB', str(CORE))
    monkeypatch.setenv('DATACORE_LEDGER_SIGN', '0')
    origin = tmp_path / 'remote.git'
    origin.mkdir()
    git(origin, 'init', '--bare', '-b', 'main')
    roots = {}
    for actor in ('work-mac', 'work-windows', 'worker'):
        root = tmp_path / actor
        registry = root / '.datacore/registry'
        registry.mkdir(parents=True)
        (root / '.datacore/lib').symlink_to(CORE, target_is_directory=True)
        (registry / 'repositories.yaml').write_text('repositories:\n  9-work: {category: knowledge}\n')
        space = root / '9-work'
        git(root, 'clone', str(origin), str(space))
        git(space, 'config', 'user.email', 'fixture@example.invalid')
        git(space, 'config', 'user.name', actor)
        roots[actor] = root
    first = roots['work-mac'] / '9-work'
    (first / 'org').mkdir()
    (first / 'org/inbox.org').write_text('#+TITLE: Inbox\n')
    (first / '.datacore/events').mkdir(parents=True)
    (first / '.datacore/events/.keep').touch()
    (first / '.datacore/ledger-phase').write_text('1\n')
    (first / '.datacore/ledger-edit-protocol').write_text('2\n')
    (first / '.gitignore').write_text('org/next_actions.org\n.datacore/state/\n')
    git(first, 'add', '.')
    git(first, 'commit', '-m', 'empty private space')
    git(first, 'push', '-u', 'origin', 'main')
    for actor in ('work-windows', 'worker'):
        git(roots[actor] / '9-work', 'pull', '--ff-only', 'origin', 'main')

    # The module handler shells out to the real Python adapter. No manually
    # fabricated item.create events stand in for the capture boundary.
    js = '''import {pathToFileURL} from 'node:url';
      const {tools} = await import(pathToFileURL(process.argv[1]));
      const tool = tools.find(t => t.name === 'add_task');
      const args = tool.inputSchema.parse(JSON.parse(process.argv[3]));
      console.log(JSON.stringify(await tool.handler(args, {storage:{basePath:process.argv[2]}})));
    '''
    ids = []
    for actor in ('work-mac', 'work-windows'):
        root = roots[actor]
        payload = {'title': f'Report from {actor}', 'space': '9-work', 'tags': ':AI:research:',
                   'body': 'Write a report with evidence.\nKeep the original question.',
                   'properties': {'SURFACE': '9-work/0-inbox/report.md',
                                  'DONE_WHEN': 'The report contains the checked result.'}}
        env = {**os.environ, 'DATACORE_ROOT': str(root), 'DATACORE_ACTOR': actor,
               'DATACORE_PYTHON': sys.executable}
        p = subprocess.run(['node', '--input-type=module', '-e', js,
                            str(ROOT / '.datacore/modules/gtd/tools/index.js'), str(root), json.dumps(payload)],
                           env=env, capture_output=True, text=True, timeout=60)
        assert p.returncode == 0, p.stderr
        result = json.loads(p.stdout)
        assert result.get('added'), result
        assert result['ledger_actor'] == actor
        ids.append(result['id'])
        # Simulate being offline: creation exists locally before any transport.
        assert result['id'] in fold(read_events(root / '9-work')).items
        monkeypatch.setenv('DATACORE_ROOT', str(root))
        monkeypatch.setenv('DATACORE_ACTOR', actor)
        sent = converge(root / '9-work', root=root)
        assert sent.ok, sent

    root = roots['worker']
    monkeypatch.setenv('DATACORE_ROOT', str(root))
    monkeypatch.setenv('DATACORE_ACTOR', 'worker')
    space = root / '9-work'
    assert converge(space, root=root).ok
    assert len(fold(read_events(space)).items) == 2
    assert 'REFUSED' not in project_space(space)
    sys.path.insert(0, str(NS))
    import claim
    import ledger_hooks
    import nightshift_parser
    assert set(ids) <= {t.id for t in nightshift_parser.find_ai_tasks(root, require_executable=True)}
    for task in nightshift_parser.parse_org_file(space / 'org/next_actions.org'):
        assert task.properties['DONE_WHEN'] == 'The report contains the checked result.'
        assert claim.claim_task(task, root)
        assert ledger_hooks.lifecycle('started', task, 'fixture-exec')
        artifact = space / '0-inbox' / f'{task.id}.md'
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text('Checked result: 2 + 2 = 4.\n')
        # Actual completion adapter must not dismiss this phase-1 item.
        claim.complete_task(task, root, 'approved', 0.95, str(artifact))
        assert fold(read_events(space)).items[task.id].status == 'claimed'
        git(space, 'add', '0-inbox')
        git(space, 'commit', '-m', 'fixture output')
        commit = git(space, 'rev-parse', 'HEAD')
        git(space, 'push', 'origin', 'main')
        assert ledger_hooks.lifecycle('completed', task, 'fixture-exec', output_path=str(artifact),
                                       artifacts=[{'commit': commit}], publication='published')
    assert all(i.status == 'completed' for i in fold(read_events(space)).items.values())
    assert 'REFUSED' not in project_space(space)
    assert all(t.state == 'REVIEW' for t in nightshift_parser.parse_org_file(space / 'org/next_actions.org'))
    assert converge(space, root=root).ok

    # A worker cannot verify its own output. The human verifies after fetching
    # the artifact and checking the retained evidence.
    for identity in ids:
        EventLog(space, 'worker').append('item.verify', {'id': identity})
    assert all(i.status == 'completed' for i in fold(read_events(space)).items.values())
    first_root = roots['work-mac']
    monkeypatch.setenv('DATACORE_ROOT', str(first_root))
    monkeypatch.setenv('DATACORE_ACTOR', 'work-mac')
    assert converge(first, root=first_root).ok
    for identity in ids:
        assert (first / '0-inbox' / f'{identity}.md').is_file()
        EventLog(first, 'work-mac').append('item.verify', {'id': identity})
    assert converge(first, root=first_root).ok
    for actor, root in roots.items():
        monkeypatch.setenv('DATACORE_ROOT', str(root))
        monkeypatch.setenv('DATACORE_ACTOR', actor)
        assert converge(root / '9-work', root=root).ok
        state = fold(read_events(root / '9-work'))
        assert all(state.items[identity].status == 'verified' for identity in ids)
        assert verify_space(root / '9-work').verdict == 'ok'
        done = [e for e in read_events(root / '9-work') if e.type == 'item.complete']
        assert len(done) == 2 and all(e.payload['output_sha256'] for e in done)
