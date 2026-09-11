import importlib.util
import io
import json
from pathlib import Path
import stat
import sys

import pytest
from hook_state import state_path


def test_hook_sessions_do_not_collide_and_state_is_private(tmp_path):
    first,second=state_path('demo-mode','a/b'),state_path('demo-mode','ab')
    assert first != second and first.parent == second.parent
    assert stat.S_IMODE(first.parent.stat().st_mode) == 0o700
    target=tmp_path/'unrelated';target.write_text('keep')
    first.symlink_to(target)
    with pytest.raises(ValueError,match='symbolic'):
        state_path('demo-mode','a/b')
    assert target.read_text() == 'keep'


def test_environment_probe_records_only_boolean_flags(monkeypatch,capsys):
    import probe_hook_env
    monkeypatch.setenv('DATACORE_PRIVATE_API_KEY','synthetic-secret')
    monkeypatch.setenv('DATACORE_HEADLESS','1')
    monkeypatch.setattr(sys,'stdin',io.StringIO('{}'))
    probe_hook_env.main()
    path=state_path('env-probe');content=path.read_text()
    assert 'synthetic-secret' not in content and 'PRIVATE_API_KEY' not in content
    assert json.loads(content)['DATACORE_HEADLESS'] is True
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert capsys.readouterr().out == '{}'


def test_demo_reminder_sticks_only_to_its_session(monkeypatch,capsys):
    path=Path(__file__).resolve().parents[1]/'hooks/redaction_guard.py'
    spec=importlib.util.spec_from_file_location('test_redaction_hook',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def invoke(session,prompt):
        monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps({'session_id':session,'prompt':prompt})))
        with pytest.raises(SystemExit): module.main()
        return capsys.readouterr().out
    assert 'demo-mode-active' in invoke('a/b','demo now')
    assert 'demo-mode-active' in invoke('a/b','next')
    assert 'demo-mode-active' not in invoke('ab','next')


def test_dispatcher_unit_references_a_real_supported_entry_point(monkeypatch):
    import os
    import shlex
    import subprocess
    root=Path(__file__).resolve().parents[3]
    source=(root/'.datacore/lib/ledger-dispatch.service').read_text()
    line=next(line.removeprefix('ExecStart=') for line in source.splitlines() if line.startswith('ExecStart='))
    command=shlex.split(line)
    script=root/command[1].split('%h/Data/',1)[1]
    assert script.is_file()
    env={**os.environ,'DATACORE_ACTOR':'synthetic-worker','DATACORE_ROOT':str(root)}
    result=subprocess.run([sys.executable,str(script),'--help'],env=env,capture_output=True,text=True,timeout=10)
    assert result.returncode == 0, result.stderr
    for option in command[2:]:
        if option.startswith('--'): assert option in result.stdout
