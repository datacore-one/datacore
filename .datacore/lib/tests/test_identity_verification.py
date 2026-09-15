"""Identity declarations describe access; equal users do not prove isolation."""
import pytest

import v2_verify


def check(tmp_path, monkeypatch, text):
    registry = tmp_path / '.datacore/registry/infrastructure.yaml'
    registry.parent.mkdir(parents=True)
    registry.write_text(text)
    monkeypatch.setattr(v2_verify, 'ROOT', tmp_path)
    report = v2_verify.Report()
    v2_verify.check_identity(report)
    return report


def test_separate_administrator_and_service_users_are_valid_declarations(tmp_path, monkeypatch):
    report = check(tmp_path, monkeypatch, '''servers:
  worker:
    ssh_alias: worker
    access:
      actor: worker
      ssh_user: administrator
      service_user: datacore-worker
''')
    assert not report.failed
    assert any(c.name == 'execution identities declared' and c.ok is True for c in report.checks)
    assert all('==' not in c.name for c in report.checks)
    assert any('runtime privileges not checked' in c.detail for c in report.checks)


@pytest.mark.parametrize('text', [
    'servers: {}\n',
    'servers: []\n',
    'servers:\n  worker: 7\n',
    'servers:\n  worker:\n    access: []\n',
    'servers:\n  worker:\n    access:\n      actor: true\n',
    'servers:\n  worker:\n    access:\n      actor: worker\n      ssh_user: admin\n',
    'servers:\n  worker:\n    access:\n      actor: worker\n      service_user: worker\n',
    'servers:\n  worker:\n    access:\n      actor: worker\n      ssh_user: admin\n      service_user: "worker; command"\n',
    'servers:\n  worker:\n    access:\n      actor: first\n      actor: second\n      ssh_user: admin\n      service_user: worker\n',
])
def test_missing_or_ambiguous_declarations_cannot_pass(tmp_path, monkeypatch, text):
    report = check(tmp_path, monkeypatch, text)
    assert report.failed


def test_explicit_local_machine_does_not_require_ssh_credentials(tmp_path, monkeypatch):
    report = check(tmp_path, monkeypatch, '''servers:
  workstation:
    ssh_alias: '-'
    access:
      actor: workstation
      service_user: local-user
''')
    assert not report.failed and not report.unknown


def test_unavailable_registry_is_unknown_and_never_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(v2_verify, 'ROOT', tmp_path)
    report = v2_verify.Report()
    v2_verify.check_identity(report)
    assert report.unknown and not any(c.ok is True for c in report.checks)
