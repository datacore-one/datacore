"""A timed-out task must not keep writing through an ordinary child process."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest
from process_run import run


def test_timeout_stops_grandchild_before_it_can_write(tmp_path):
    marker=tmp_path/'late-write'
    ready=tmp_path/'ready'
    child="import pathlib,time; pathlib.Path("+repr(str(ready))+").write_text('ready'); time.sleep(1); pathlib.Path("+repr(str(marker))+").write_text('late')"
    parent="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',"+repr(child)+"]); time.sleep(10)"
    with pytest.raises(subprocess.TimeoutExpired):
        run([sys.executable,'-c',parent],capture_output=True,text=True,timeout=0.5)
    assert ready.read_text() == 'ready'
    time.sleep(0.8)
    assert not marker.exists()


def test_process_runner_preserves_stdin_output_and_error_contracts():
    result=run([sys.executable,'-c','import sys; print(sys.stdin.read()); print("diagnostic",file=sys.stderr)'],input='private input',capture_output=True,text=True,timeout=3,check=True)
    assert result.stdout == 'private input\n' and result.stderr == 'diagnostic\n'
    with pytest.raises(subprocess.CalledProcessError) as caught:
        run([sys.executable,'-c','import sys; print("failure"); sys.exit(7)'],capture_output=True,text=True,timeout=3,check=True)
    assert caught.value.returncode == 7 and caught.value.stdout == 'failure\n'


def test_job_envelope_has_a_finite_timeout_and_reports_failure(tmp_path,monkeypatch,capsys):
    import importlib.util
    path=Path(__file__).resolve().parents[1]/'jobs/run.py'
    spec=importlib.util.spec_from_file_location('audit_job_runner',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    job={'name':'synthetic','cmd':'sleep 10','schedule':'hourly','machine':'test','artifacts':[],'timeout_seconds':0.1}
    assert module.run(job) == 2
    assert 'timed out' in capsys.readouterr().out
    job['timeout_seconds']=float('inf')
    assert module.run(job) == 3
