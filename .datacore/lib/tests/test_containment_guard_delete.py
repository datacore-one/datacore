"""A contained task may delete only inside its workspace (Phase 5A, break 3).

The rogue-agent simulation (2026-10-03): a single-file `rm` was not classified
by any effect (data.delete covers recursive deletes outside temp folders), so
an agent could remove another space's file and nothing refused it. For a
contained overnight task the guard now refuses every kind of delete whose
target is outside the task's workspace -- its own space, its task worktrees,
temp folders and package caches -- or cannot be located.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
import tool_policy  # noqa: E402


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'state'))
    # pytest's own temp folder is /tmp on Linux, which the guard treats as the
    # task's scratch space; the fixture stands for ~/Data, so /tmp is moved aside.
    import tool_containment
    monkeypatch.setattr(tool_containment, '_TEMP_ROOTS', ('/nonexistent-temp-root',))
    data = tmp_path / 'Data'
    own = data / '3-fds'
    other = data / '1-datafund'
    worktree = tmp_path / 'worktrees' / 'fairdrive-1a2b3c4d' / 'task-5'
    for d in (own / 'notes', other / 'notes', worktree / 'node_modules'):
        d.mkdir(parents=True)
    (other / 'notes' / 'theirs.md').write_text('x')
    policy = tmp_path / 'policy.yaml'
    policy.write_text('version: 1\napprover: human\ncosign_effects: []\nprincipals:\n  worker: {}\n')
    env = {'DATACORE_POLICY_PRINCIPAL': 'worker', 'DATACORE_POLICY_CONTAINED': '1',
           'DATACORE_POLICY_TASK': 'task-5', 'DATACORE_POLICY_SPACE': str(own),
           'DATACORE_POLICY_EGRESS': 'github.com',
           'DATACORE_POLICY_WORKSPACE': os.pathsep.join([str(own), str(worktree)]),
           'DATACORE_POLICY_CWD': str(data)}
    return env, policy, data, own, other, worktree


def _hook(command, env, policy, cwd=None):
    payload = {'tool_name': 'Bash', 'tool_input': {'command': command}}
    if cwd:
        payload['cwd'] = str(cwd)
    return tool_policy.evaluate_hook(payload, env=env, record=False, policy_path=policy)


@pytest.mark.parametrize('command', [
    'rm {other}/notes/theirs.md',
    'rm -f ../1-datafund/notes/theirs.md',
    'unlink {other}/notes/theirs.md',
    'shred -u {other}/notes/theirs.md',
    'git -C {other} rm notes/theirs.md',
    'find {other}/notes -name "*.md" -delete',
    'mv {other}/notes/theirs.md /tmp/',
    'rmdir {other}/notes',
    'truncate -s 0 {other}/notes/theirs.md',
    'rm -rf "$TARGET"',
    'cd {own} && rm ../1-datafund/notes/theirs.md',
])
def test_a_delete_outside_the_workspace_is_refused(command, setup):
    env, policy, data, own, other, worktree = setup
    cmd = command.format(other=other, own=own)
    out = _hook(cmd, env, policy, cwd=own)
    assert out is not None, f'{cmd!r} passed the guard'
    assert 'outside this task' in out['hookSpecificOutput']['permissionDecisionReason']


@pytest.mark.parametrize('command,where', [
    ('rm -rf node_modules dist', 'worktree'),
    ('rm {worktree}/old.txt', 'own'),
    ('rm notes/draft.md', 'own'),
    ('rm -rf /nonexistent-temp-root/fairdrive-build', 'own'),
    ('git rm -q docs/obsolete.md', 'worktree'),
    ('find . -name "*.pyc" -delete', 'worktree'),
    ('mv notes/a.md notes/b.md', 'own'),
])
def test_deletes_inside_the_workspace_still_pass(command, where, setup):
    env, policy, data, own, other, worktree = setup
    cwd = worktree if where == 'worktree' else own
    cmd = command.format(worktree=worktree)
    assert _hook(cmd, env, policy, cwd=cwd) is None, cmd
