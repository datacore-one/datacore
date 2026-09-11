"""Dependency checks must reproduce the job's import order and requirements."""
import subprocess
import sys
from pathlib import Path

import pytest

import host_deps
import venv_bootstrap


def package(root, version, body=''):
    root.mkdir(parents=True, exist_ok=True)
    (root / 'audit_dependency.py').write_text(body)
    info = root / f'audit_dependency-{version}.dist-info'
    info.mkdir()
    (info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: audit-dependency\nVersion: {version}\n')


def test_foreign_python_venv_is_never_imported(tmp_path):
    (tmp_path / 'venv/lib/python99.0/site-packages').mkdir(parents=True)
    assert venv_bootstrap.venv_site_packages(str(tmp_path)) is None
    before = list(sys.path)
    assert not venv_bootstrap.activate(str(tmp_path))
    assert sys.path == before


def test_exact_minor_selected_not_lexicographic_latest(tmp_path, monkeypatch):
    monkeypatch.setattr(venv_bootstrap.sysconfig, 'get_config_var', lambda key: None)
    compatible = tmp_path / f'venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    compatible.mkdir(parents=True)
    (tmp_path / 'venv/lib/python99.0/site-packages').mkdir(parents=True)
    assert venv_bootstrap.venv_site_packages(str(tmp_path)) == str(compatible)


def test_free_threaded_abi_does_not_use_gil_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(venv_bootstrap.sysconfig, 'get_config_var', lambda key: 1)
    plain = tmp_path / f'venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages'
    plain.mkdir(parents=True)
    assert venv_bootstrap.venv_site_packages(str(tmp_path)) is None
    compatible = Path(str(plain).replace('/site-packages', 't/site-packages'))
    compatible.mkdir(parents=True)
    assert venv_bootstrap.venv_site_packages(str(tmp_path)) == str(compatible)


def test_newer_fallback_cannot_hide_outdated_runtime_dependency(tmp_path, monkeypatch):
    runtime, fallback = tmp_path / 'runtime', tmp_path / 'fallback'
    package(runtime, '1.0')
    package(fallback, '2.0')
    monkeypatch.setenv('PYTHONPATH', str(runtime))
    monkeypatch.setattr(host_deps, '_venv_site_packages', lambda: str(fallback))
    ok, _, reason = host_deps.importable('audit-dependency>=2')
    assert not ok
    assert 'installed 1.0' in reason


def test_fallback_and_real_import_failure(tmp_path, monkeypatch):
    fallback = tmp_path / 'fallback'
    package(fallback, '2.0', 'raise RuntimeError("incompatible ABI")\n')
    monkeypatch.delenv('PYTHONPATH', raising=False)
    monkeypatch.setattr(host_deps, '_venv_site_packages', lambda: str(fallback))
    ok, _, reason = host_deps.importable('audit-dependency>=2')
    assert not ok and 'incompatible ABI' in reason
    (fallback / 'audit_dependency.py').write_text('')
    assert host_deps.importable('audit-dependency>=2') == (True, 'venv', '')


def test_requirements_keep_versions_markers_and_includes(tmp_path):
    (tmp_path / 'more.txt').write_text('audit-dependency>=2,<3\n')
    path = tmp_path / 'requirements.txt'
    path.write_text('-r more.txt\nnever-installed; python_version < "1"\n')
    assert host_deps.parse(path) == ['audit-dependency<3,>=2']
    (tmp_path / 'more.txt').write_text('-r requirements.txt\n')
    with pytest.raises(ValueError, match='recursive'):
        host_deps.parse(path)
    path.write_text('-r missing.txt\n')
    with pytest.raises(FileNotFoundError):
        host_deps.parse(path)


def test_unverifiable_sources_are_not_passes(tmp_path):
    path = tmp_path / 'requirements.txt'
    path.write_text('audit-dependency @ https://example.invalid/package.whl\n')
    with pytest.raises(ValueError, match='source identity'):
        host_deps.parse(path)


def test_hung_import_is_failure(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 180)
    monkeypatch.setattr(host_deps.subprocess, 'run', timeout)
    assert host_deps.importable('audit-dependency>=2') == (False, '', 'import timed out after 180s')


def test_empty_profile_is_not_healthy(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(host_deps, 'DATACORE_ROOT', tmp_path)
    monkeypatch.setattr(sys, 'argv', ['host_deps'])
    assert host_deps.main() == 1
    assert 'empty' in capsys.readouterr().err
