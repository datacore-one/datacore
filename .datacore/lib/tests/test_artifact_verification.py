"""Artifact verification observes a committed result without staging user data."""
from pathlib import Path
import subprocess
import pytest
import ledger_claim


def repository(tmp_path):
    def git(*args):
        return subprocess.run(['git','-c','core.hooksPath=/dev/null','-C',str(tmp_path),*args],capture_output=True,text=True,check=True).stdout
    git('init','-q');git('config','user.email','audit@example.invalid');git('config','user.name','Synthetic audit')
    (tmp_path/'proof.txt').write_text('verified\n');git('add','proof.txt');git('commit','-qm','baseline')
    return git


def test_uncommitted_result_cannot_verify_old_head_or_stage_other_work(tmp_path):
    git=repository(tmp_path)
    (tmp_path/'proof.txt').write_text('unverified\n')
    (tmp_path/'unrelated.txt').write_text('private unrelated content');git('add','unrelated.txt')
    before_head=git('rev-parse','HEAD');before_index=git('diff','--cached','--binary')
    assert ledger_claim._isolated_check(tmp_path,'grep -qx verified proof.txt') == (False,'')
    assert git('rev-parse','HEAD') == before_head
    assert git('diff','--cached','--binary') == before_index
    assert (tmp_path/'proof.txt').read_text() == 'unverified\n'


def test_check_runs_in_captured_commit_and_preserves_active_ledger(tmp_path):
    git=repository(tmp_path)
    events=tmp_path/'.datacore/events';events.mkdir(parents=True)
    (events/'worker.jsonl').write_text('synthetic active append\n')
    passed,sha=ledger_claim._isolated_check(tmp_path,'grep -qx verified proof.txt && printf changed > proof.txt')
    assert passed and sha == git('rev-parse','HEAD').strip()
    assert (tmp_path/'proof.txt').read_text() == 'verified\n'
    assert (events/'worker.jsonl').read_text() == 'synthetic active append\n'
    assert len([line for line in git('worktree','list','--porcelain').splitlines() if line.startswith('worktree ')]) == 1


def test_check_timeout_is_a_failure_and_removes_disposable_worktree(tmp_path,monkeypatch):
    git=repository(tmp_path)
    monkeypatch.setattr(ledger_claim,'run_process',lambda *a,**kw: (_ for _ in ()).throw(subprocess.TimeoutExpired('synthetic',120)))
    passed,sha=ledger_claim._isolated_check(tmp_path,'sleep 999')
    assert not passed and sha == git('rev-parse','HEAD').strip()
    assert len([line for line in git('worktree','list','--porcelain').splitlines() if line.startswith('worktree ')]) == 1


def test_a_failing_check_reports_its_own_error_without_crashing(tmp_path):
    """The check's stderr is the diagnosis, and reading it must not raise.

    `run_process(..., capture_output=True)` returns BYTES here; the first
    version concatenated them as str and raised TypeError mid-dispatch, which
    left the item claimed with no completion -- the one state this module works
    hardest to avoid. A diagnostic that can crash the run is worse than none.
    """
    git = repository(tmp_path)
    passed, sha = ledger_claim._isolated_check(tmp_path, 'cat no-such-file-here')
    assert passed is False
    assert sha, "a failing check still reports the head it checked"


def test_a_passing_check_needs_no_diagnosis(tmp_path):
    git = repository(tmp_path)
    passed, sha = ledger_claim._isolated_check(tmp_path, 'grep -qx verified proof.txt')
    assert passed is True
