import pytest
from server_setup import plan, apply


def test_profile_is_portable_and_has_only_explicit_service_writers(tmp_path):
    root = tmp_path / 'Data with spaces'
    (root / '0-personal/org').mkdir(parents=True)
    doc = plan(root, 'owner', 'assistant', 'worker', 'mini')
    assert len(doc['schedules']) == 3
    assert {j['environment']['DATACORE_ACTOR'] for j in doc['schedules']} == {'assistant', 'worker'}
    assert all(j['environment']['DATACORE_ROOT'] == str(root) for j in doc['schedules'])
    assert all(j['environment']['DATACORE_LEDGER_SIGN'] == '0' for j in doc['schedules'])
    assert "'" in doc['schedules'][-1]['command']
    assert not (root / '.datacore/config/server.local.yaml').exists(), 'plan must not write configuration'


def test_setup_refuses_missing_dependencies_before_writing(tmp_path, monkeypatch):
    (tmp_path / '0-personal/org').mkdir(parents=True)
    doc = plan(tmp_path, 'owner', 'assistant', 'worker', 'mini')
    monkeypatch.setenv('DATACORE_ROOT', str(tmp_path))
    with pytest.raises(ValueError, match='prerequisites'):
        apply(doc)
    assert not (tmp_path / '.datacore/registry/principals.yaml').exists()


def test_service_roles_cannot_share_a_writer(tmp_path):
    (tmp_path / '0-personal/org').mkdir(parents=True)
    with pytest.raises(ValueError, match='distinct'):
        plan(tmp_path, 'owner', 'worker', 'worker', 'mini')


def test_apply_registers_writers_is_repeatable_and_preserves_operator_edits(tmp_path, monkeypatch):
    import server_setup
    import yaml
    root = tmp_path / 'Data'
    (root / '0-personal/org').mkdir(parents=True)
    identity = tmp_path / 'identity.env'
    monkeypatch.setenv('DATACORE_ROOT', str(root))
    monkeypatch.setenv('DATACORE_IDENTITY_FILE', str(identity))
    monkeypatch.delenv('DATACORE_ACTOR', raising=False)
    # Dependency probing is covered by the refusal test; use installed code
    # against isolated configuration and the real identity/registry writers.
    monkeypatch.setattr(server_setup, 'prerequisites', lambda root: [])
    doc = plan(root, 'owner', 'assistant', 'worker', 'mini')
    paths = apply(doc)
    assert apply(doc) == paths
    registry = yaml.safe_load((root / '.datacore/registry/principals.yaml').read_text())
    assert registry['principals']['owner']['kind'] == 'human'
    assert registry['principals']['worker']['kind'] == 'agent'
    assert 'DATACORE_ACTOR=assistant' in identity.read_text()
    schedule = root / '.datacore/modules/nightshift/schedules.local.yaml'
    schedule.write_text('schedules: []\n')
    with pytest.raises(ValueError, match='differs'):
        apply(doc)
    assert schedule.read_text() == 'schedules: []\n'
