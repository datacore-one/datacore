"""Publication/retry and source-boundary invariants for operator controls."""
import multiprocessing
import os
import stat
from pathlib import Path

import pytest
import execution_controls as controls
import file_utils


@pytest.fixture(autouse=True)
def private_state(monkeypatch, tmp_path):
    monkeypatch.setenv('DATACORE_STATE', str(tmp_path / 'private'))
    monkeypatch.setenv('DATACORE_ROOT', str(tmp_path / 'data'))


@pytest.mark.parametrize('kind', ['symbolic', 'hard', 'fifo', 'directory'])
def test_policy_read_refuses_aliases_and_special_sources(tmp_path, kind):
    path = tmp_path / 'policies.yaml'
    other = tmp_path / 'other'; other.write_text('enabled: false\n')
    if kind == 'symbolic': path.symlink_to(other)
    elif kind == 'hard': os.link(other, path)
    elif kind == 'fifo': os.mkfifo(path)
    else: path.mkdir()
    with pytest.raises(controls.PolicyError): controls.read_policy(path)
    assert other.read_text() == 'enabled: false\n'


def test_policy_cannot_use_an_aliased_ancestor(tmp_path):
    source = tmp_path / 'real/nested/policies.yaml'
    source.parent.mkdir(parents=True)
    source.write_text('enabled: false\n')
    alias = tmp_path / 'alias'; alias.symlink_to(source.parent.parent)
    path = alias / 'nested/policies.yaml'
    with pytest.raises(controls.PolicyError): controls.read_policy(path)
    with pytest.raises(controls.PolicyError): controls.update_policy(path, lambda d: d.update(enabled=True))
    assert source.read_text() == 'enabled: false\n'


@pytest.mark.parametrize('source', ['extension: &x [*x]', 'extension: .nan',
                                   'attention_overrides: {fixture: 5}',
                                   'attention_overrides: {fixture: {force_band: unknown}}'])
def test_policy_read_rejects_ambiguous_or_invalid_retained_values(tmp_path, source):
    path = tmp_path / 'policies.yaml'; path.write_text(source)
    with pytest.raises(controls.PolicyError): controls.read_policy(path)
    assert path.read_text() == source


def test_failed_publication_preserves_original_and_retry(tmp_path, monkeypatch):
    path = tmp_path / 'policies.yaml'
    before = '# retain\nenabled: false\npaused_cadences: []\n'; path.write_text(before)
    replace = file_utils.os.replace
    def fail(*a, **kw): raise OSError('fixture storage failure')
    monkeypatch.setattr(file_utils.os, 'replace', fail)
    with pytest.raises(controls.PolicyError): controls.update_policy(path, lambda d: d['paused_cadences'].append('v:daily'))
    assert path.read_text() == before
    monkeypatch.setattr(file_utils.os, 'replace', replace)
    controls.update_policy(path, lambda d: d['paused_cadences'].append('v:daily'))
    assert controls.read_policy(path)['paused_cadences'] == ['v:daily']
    assert '# retain' in path.read_text()


def test_concurrent_manual_change_is_not_overwritten(tmp_path):
    path = tmp_path / 'policies.yaml'; path.write_text('enabled: true\n')
    def change(data):
        data['paused_cadences'] = ['v:daily']
        path.write_text('enabled: false\n')
    with pytest.raises(controls.PolicyError): controls.update_policy(path, change)
    assert path.read_text() == 'enabled: false\n'


def test_invalid_mutator_result_is_not_published(tmp_path):
    path = tmp_path / 'policies.yaml'; path.write_text('enabled: false\n')
    with pytest.raises(controls.PolicyError): controls.update_policy(path, lambda d: d.update(enabled='yes'))
    assert path.read_text() == 'enabled: false\n'


def _parallel_update(path, number):
    controls.update_policy(path, lambda d: d.setdefault('paused_cadences', []).append(f'v:{number}'))


def test_process_writers_retain_all_acknowledged_edits(tmp_path):
    path = tmp_path / 'new-cos/policies.yaml'
    ctx = multiprocessing.get_context('fork')
    children = [ctx.Process(target=_parallel_update, args=(path, n)) for n in range(8)]
    for p in children: p.start()
    for p in children:
        p.join(15)
        if p.is_alive(): p.kill(); p.join(); pytest.fail('policy writer failed to terminate')
        assert p.exitcode == 0
    assert set(controls.read_policy(path)['paused_cadences']) == {f'v:{n}' for n in range(8)}
    assert len(controls.read_policy(path)['paused_cadences']) == 8


def _die_before_replacement(path):
    file_utils.os.replace = lambda *a, **kw: os._exit(23)
    controls.update_policy(path, lambda d: d.update(enabled=True))


def test_crash_before_replacement_retains_controls_and_releases_lock(tmp_path):
    path = tmp_path / 'policies.yaml'; before = '# retain\nenabled: false\n'; path.write_text(before)
    ctx = multiprocessing.get_context('fork')
    child = ctx.Process(target=_die_before_replacement, args=(path,)); child.start(); child.join(15)
    if child.is_alive(): child.kill(); child.join(); pytest.fail('crash probe failed to terminate')
    assert child.exitcode == 23
    assert path.read_text() == before
    controls.update_policy(path, lambda d: d.update(paused_cadences=['v:daily']))
    assert controls.read_policy(path)['enabled'] is False


def test_directory_flush_failure_is_not_acknowledged(tmp_path, monkeypatch):
    path = tmp_path / 'policies.yaml'; path.write_text('enabled: false\n')
    controls.update_policy(path, lambda d: None)  # establish private lock directories
    original = file_utils.os.fsync
    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError('fixture durability failure')
        original(fd)
    monkeypatch.setattr(file_utils.os, 'fsync', fail_directory)
    with pytest.raises(controls.PolicyError): controls.update_policy(path, lambda d: d.update(paused_cadences=['v:daily']))
    assert controls.read_policy(path)['enabled'] is False
    assert controls.read_policy(path)['paused_cadences'] == ['v:daily']
