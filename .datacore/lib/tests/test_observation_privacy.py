"""Observation content cannot cross into logs or become unreviewed memory."""
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import sys
from datetime import datetime, timezone

import pytest
import observation_metadata as metadata

LIB = Path(__file__).resolve().parents[1]


def hook_module():
    spec = importlib.util.spec_from_file_location('observation_hook_fixture', LIB / 'hooks/plur_observe.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def observer(tmp_path, monkeypatch):
    module = hook_module()
    monkeypatch.setattr(module, 'OBS_DIR', tmp_path / 'observations')
    monkeypatch.setenv('CLAUDE_SESSION_ID', 'private-session-credential')
    monkeypatch.chdir(tmp_path)
    return module


@pytest.mark.parametrize('event', ['PreToolUse', 'PostToolUse', 'PostToolUseFailure'])
def test_sensitive_values_never_enter_metadata_or_output(observer, monkeypatch, capsys, event):
    private = {'tool_name': 'Bash', 'tool_input': {'command': 'private-command-credential',
               'nested': {'password': 'private-nested-token'}}, 'error': 'private-error-token',
               'cwd': '/private/customer-directory'}
    monkeypatch.setenv('CLAUDE_HOOK_EVENT_NAME', event)
    monkeypatch.setattr(sys, 'stdin', io.StringIO(json.dumps(private)))
    observer.main([])
    captured = capsys.readouterr()
    assert captured.out == '{}\n' and not captured.err
    records = metadata.load(observer.OBS_DIR, 1)
    assert len(records) == 1 and records[0]['tool'] == 'Bash' and records[0]['event'] == event
    content = json.dumps(records)
    for secret in ['private-command', 'private-nested', 'private-error', 'private-session', 'customer-directory', str(Path.cwd())]:
        assert secret not in content
    assert stat.S_IMODE(observer.OBS_DIR.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in observer.OBS_DIR.iterdir())


@pytest.mark.parametrize('payload', ['null', '[]', 'true', '1', '{"tool_name":[]}',
                                    '[' * 2000 + '0' + ']' * 2000, 'x' * (512 * 1024 + 1)])
def test_malformed_hook_input_cannot_log_or_echo_content(observer, monkeypatch, capsys, payload):
    monkeypatch.setattr(sys, 'stdin', io.StringIO(payload))
    observer.main([])
    captured = capsys.readouterr()
    assert captured.out == '{}\n'
    assert not observer.OBS_DIR.exists()
    assert not captured.err or captured.err == 'Observation metadata unavailable\n'


def test_unknown_tool_name_is_coarsened(observer, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"tool_name":"private-customer-token"}'))
    observer.main([])
    assert metadata.load(observer.OBS_DIR, 1)[0]['tool'] == 'External'


def test_legacy_data_is_preserved_contained_and_excluded_from_learning(observer, monkeypatch):
    root = observer.OBS_DIR
    root.mkdir(mode=0o755)
    old = root / (datetime.now(timezone.utc).strftime('%Y-%m-%d') + '.jsonl')
    legacy = '{"tool":"Bash","error":"private-historical-error"}\n'
    old.write_text(legacy)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"tool_name":"Bash"}'))
    observer.main([])
    assert old.read_text() == legacy
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    for name in ('plur_auto_promote', 'plur_observation_analyzer'):
        module = __import__(name)
        monkeypatch.setattr(module, 'OBS_DIR', root)
        loaded = module.load_observations(1)
        assert len(loaded) == 1 and loaded[0]['schema'] == 2
        assert 'private-historical' not in json.dumps(loaded)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'fifo', 'public'])
def test_hostile_log_inode_is_refused_without_target_modification(observer, tmp_path, kind):
    root = observer.OBS_DIR;root.mkdir(mode=0o700)
    file = root / (datetime.now(timezone.utc).strftime('%Y-%m-%d') + '.metadata.jsonl')
    victim = tmp_path / 'victim';victim.write_text('preserved');victim.chmod(0o600)
    if kind == 'symlink': file.symlink_to(victim)
    elif kind == 'hardlink': os.link(victim, file)
    elif kind == 'fifo': os.mkfifo(file, 0o600)
    else: file.write_text('unrelated');file.chmod(0o644)
    value = metadata.record({'tool_name':'Bash'}, 'PreToolUse', 'session', 'workspace')
    with pytest.raises((OSError, ValueError)):
        metadata.append(root, value)
    assert victim.read_text() == 'preserved'


@pytest.mark.parametrize('field,value', [('event', []), ('tool', {}), ('workspace', 'secret'),
                                        ('schema', True), ('ts', 'secret'), ('input', {'token':'secret'})])
def test_forged_metadata_cannot_inject_content(field, value):
    row = metadata.record({'tool_name':'Bash'}, 'PreToolUse', 'session', 'workspace')
    row[field] = value
    assert metadata.validated(row) is None


def test_cross_workspace_candidates_require_review(monkeypatch, capsys):
    import plur_auto_promote as promote
    rows = []
    for workspace in ['workspace-a', 'workspace-b']:
        rows.extend(metadata.record({'tool_name':'Bash'}, 'PostToolUseFailure', 'session', workspace) for _ in range(5))
    candidate = {'type':'behavioral', 'scope':'global', 'statement':'Review this fixture',
                 'spaces':['workspace-a','workspace-b'], 'confidence':0.7, 'rationale':'Synthetic pattern'}
    monkeypatch.setattr(promote, 'load_observations', lambda *a: rows)
    monkeypatch.setattr(promote, 'generate_promotion_candidates', lambda *a: [candidate])
    monkeypatch.setattr(promote, 'create_engram_via_mcp', lambda *a: pytest.fail('published without review'))
    monkeypatch.setattr(sys, 'argv', ['plur_auto_promote.py'])
    promote.main()
    assert 'Review required' in capsys.readouterr().out


def test_shipped_observation_hooks_use_private_metadata_producer():
    settings = json.loads((LIB.parent / 'settings.json').read_text())
    count = 0
    for entries in settings['hooks'].values():
        for entry in entries:
            for hook in entry['hooks']:
                assert 'hook-observe' not in hook['command']
                if 'plur_observe.py' in hook['command']:
                    count += 1
    assert count == 2


def test_interrupted_tail_cannot_swallow_next_acknowledged_record(tmp_path, monkeypatch):
    root = tmp_path / 'observations'
    first = metadata.record({'tool_name':'Bash'}, 'PreToolUse', 'session', 'workspace')
    write = metadata.os.write
    calls = []
    def interrupt(fd, data):
        if not calls:
            calls.append(True)
            return write(fd, data[:12])
        raise OSError('synthetic disk interruption')
    with monkeypatch.context() as patch:
        patch.setattr(metadata.os, 'write', interrupt)
        with pytest.raises(OSError, match='synthetic disk interruption'):
            metadata.append(root, first)
    second = metadata.record({'tool_name':'Edit'}, 'PostToolUse', 'session', 'workspace')
    metadata.append(root, second)
    assert metadata.load(root, 1) == [second]
    content = next(root.glob('*.metadata.jsonl')).read_bytes()
    assert len(content.splitlines()) == 2  # interrupted bytes retained


def test_sequences_never_join_across_workspaces_or_unidentified_sessions():
    import plur_auto_promote as promote
    import plur_observation_analyzer as analyzer
    rows = []
    # Joining the same session's events across workspaces invents a repeated
    # three-tool sequence; each actual workspace has only one tool in it.
    for _ in range(8):
        for tool, workspace in [('Bash','a'),('Edit','b'),('Write','c')]:
            rows.append(metadata.record({'tool_name':tool}, 'PreToolUse', 'shared', workspace))
    sequences = analyzer.analyze_sequences(rows)
    assert ('Bash','Edit','Write') not in sequences
    promoted = promote.find_cross_space_tool_sequences(rows, 2)
    assert 'Bash → Edit → Write' not in promoted
    for row in rows: row['session_id'] = ''
    assert analyzer.analyze_sequences(rows) == {}
    assert promote.find_cross_space_tool_sequences(rows, 2) == {}


@pytest.mark.parametrize('command,args', [
    ('npx @plur-ai/cli hook-observe >/dev/null', []),
    ('npx --yes @plur-ai/cli@0.19.4 hook-observe --post > /dev/null', ['--post']),
    ('/opt/plur/bin/plur-hook hook-observe --failure', ['--failure']),
])
def test_installer_migrates_existing_observer_without_adding_global_observers(command, args):
    import shlex
    spec = importlib.util.spec_from_file_location('observation_installer_fixture', LIB / 'bootstrap/configure-hooks.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    required = module.build_required_hooks('/synthetic/install with spaces')
    neighbor = {'command':'echo preserve', 'timeout':4}
    settings = {'hooks':{'PreToolUse':[{'matcher':'Bash','hooks':[{'command':command,'timeout':3},neighbor]}]}}
    result, _, upgraded = module.merge_hooks(settings, required)
    original_entry = result['hooks']['PreToolUse'][0]
    actual = shlex.split(original_entry['hooks'][0]['command'])
    assert actual[:2] == ['python3','/synthetic/install with spaces/.datacore/lib/hooks/plur_observe.py']
    assert actual[2:2+len(args)] == args
    assert neighbor in original_entry['hooks'] and upgraded
    clean, _, _ = module.merge_hooks({}, required)
    assert not any('plur_observe.py' in h['command'] for entries in clean['hooks'].values()
                   for entry in entries for h in entry['hooks'])


def test_installer_fallback_selects_its_repository_not_datacore_subdirectory(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('observation_installer_fallback', LIB / 'bootstrap/configure-hooks.py')
    module = importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setattr(sys, 'argv', ['configure-hooks.py'])
    assert Path(module.detect_datacore_root()) == LIB.parents[1]
    assert module._observation_replacement(None, module.build_required_hooks('/fixture')) is None
