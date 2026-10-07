"""Fresh MCP processes preserve one run and its arguments across tool profiles.

This tests the shared protocol, not the model/GUI clients. Live client evidence
is recorded separately; a protocol pass must never stand in for that evidence.
"""
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_client import McpClient

LIB = Path(__file__).resolve().parents[1]
ROOT = LIB.parents[1]
SERVER = ROOT / '2-datacore/2-projects/datacore-mcp/dist/index.js'


def result(response):
    payload = response['result']
    assert not payload.get('isError'), payload
    value = json.loads(payload['content'][-1]['text'])
    assert 'error' not in value, value
    return value


def test_new_clients_resume_the_same_run_without_repeating_steps(tmp_path):
    root = tmp_path / 'Data'
    commands = root / '.datacore/commands'
    commands.mkdir(parents=True)
    (root / '.datacore/lib').symlink_to(LIB, target_is_directory=True)
    space = root / '0-personal'
    (space / '.datacore').mkdir(parents=True)
    (space / '.datacore/config.yaml').write_text('space:\n  name: personal\n  type: personal\n')
    (space / 'notes/journals').mkdir(parents=True)
    body = '# Handoff\n\n' + '\n'.join(f'## Step {n}: Artifact {n}' for n in range(1, 5))
    (commands / 'handoff.md').write_text(body)
    env = {**os.environ, 'DATACORE_PATH': str(root), 'DATACORE_LIB': str(LIB),
           'DATACORE_PYTHON': sys.executable}
    assert SERVER.is_file(), 'Build datacore-mcp before running this integration test'
    run_id = None
    # Historical date proves status(run_id) does not depend on today's resume.
    day = '2026-09-28'
    for n, profile in enumerate(('full', 'lean', 'cursor', 'full'), 1):
        client = McpClient([shutil.which('node'), str(SERVER)],
                           env={**env, 'DATACORE_TOOL_PROFILE': profile}, cwd=str(root))
        try:
            client.initialize()
            advertised = client.request('tools/list')['result']['tools']
            loader = next(t for t in advertised if t['name'] == 'datacore_command_run')
            assert loader['annotations']['readOnlyHint'] is True
            assert 'arguments' in loader['inputSchema']['properties']
            loaded = result(client.call('datacore_command_run', {'command': '/handoff', 'arguments': f'client {n}'}))
            assert loaded['instructions'] == body
            assert loaded['arguments'] == f'client {n}'
            if run_id is None:
                run_id = result(client.call('datacore_command_steps',
                    {'op': 'start', 'command': 'handoff', 'date': day}))['result']['run_id']
            prior = result(client.call('datacore_command_steps', {'op': 'status', 'run_id': run_id}))['result']
            assert prior['done'] == [str(i) for i in range(1, n)]
            assert prior['next'] == str(n)
            artifact = root / f'artifact-{n}.txt'
            with artifact.open('x') as output:
                output.write(loaded['arguments'])
            result(client.call('datacore_command_steps',
                {'op': 'tick', 'run_id': run_id, 'steps': [str(n)], 'note': artifact.name}))
            after = result(client.call('datacore_command_steps', {'op': 'status', 'run_id': run_id}))['result']
            assert after['done'] == [str(i) for i in range(1, n + 1)]
            assert after['complete'] == (n == 4)
        finally:
            client.close()
    journal = space / f'notes/journals/{day}.md'
    assert journal.read_text().count('<!-- command-steps ') == 1
    assert len(list(root.glob('artifact-*.txt'))) == 4
