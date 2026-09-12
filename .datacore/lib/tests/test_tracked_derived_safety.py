"""A tracking scan cannot report success without reading the repository index."""
import subprocess
import pytest
from structural_integrity import StructuralIntegrityChecker


@pytest.mark.parametrize('failure', ['exit', 'missing', 'timeout'])
def test_unavailable_index_is_an_explicit_failure(tmp_path, monkeypatch, failure):
    def run(*a, **kw):
        if failure == 'missing': raise FileNotFoundError('synthetic')
        if failure == 'timeout': raise subprocess.TimeoutExpired('git', 10)
        return subprocess.CompletedProcess(a, 128, '', '')
    monkeypatch.setattr(subprocess, 'run', run)
    checker = StructuralIntegrityChecker(tmp_path)
    checker._check_tracked_derived_files()
    assert len(checker.issues) == 1 and checker.issues[0].severity == 'error'
    assert checker.issues[0].check_type == 'tracked_derived_unverified'


def test_tracks_sqlite_sidecars_without_removing_any_data(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    root = tmp_path/'.datacore';root.mkdir()
    names = ['knowledge.db', 'knowledge.db-journal', 'knowledge.db-wal', 'knowledge.db-shm']
    for name in names: (root/name).write_text('preserve these bytes')
    subprocess.run(['git', 'add', '.datacore'], cwd=tmp_path, check=True)
    checker = StructuralIntegrityChecker(tmp_path)
    checker._check_tracked_derived_files()
    assert {issue.path.name for issue in checker.issues} == set(names)
    assert all(issue.severity == 'error' and not issue.auto_fixable for issue in checker.issues)
    assert all((root/name).read_text() == 'preserve these bytes' for name in names)
