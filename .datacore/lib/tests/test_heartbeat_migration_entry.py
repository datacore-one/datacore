"""Migration uses canonical spaces and the installed state writer, never data code."""
from types import SimpleNamespace

import pytest

import migrate_heartbeat_shards as migration


def declared(root, relative, name):
    space = root / relative
    (space / '.datacore').mkdir(parents=True)
    (space / '.datacore/config.yaml').write_text(f'space: {{name: {name}, type: team}}\n')
    return space


def test_migration_covers_marked_nested_spaces_and_forwards_dry_run(tmp_path, monkeypatch):
    root = tmp_path / 'Data'
    space = declared(root, 'nested/venture', 'fixture')
    calls = []
    monkeypatch.setattr(migration, '_state_writer', lambda: SimpleNamespace(
        migrate_heartbeat=lambda path, **kw: calls.append((path, kw)) or None))
    assert migration.main(['--data-dir', str(root)]) == 0
    assert calls == [(space, {'dry_run': True})]
    assert migration.main(['--data-dir', str(root), '--apply']) == 0
    assert calls[-1] == (space, {'dry_run': False})


def test_one_invalid_space_reports_failure_and_does_not_starve_other_spaces(tmp_path, monkeypatch, capsys):
    root = tmp_path / 'Data'
    first = declared(root, 'first', 'first')
    second = declared(root, 'second', 'second')
    calls = []
    def migrate(path, **kwargs):
        calls.append(path)
        if path == first:
            raise ValueError('PRIVATE source value')
        return None
    monkeypatch.setattr(migration, '_state_writer', lambda: SimpleNamespace(migrate_heartbeat=migrate))
    assert migration.main(['--data-dir', str(root), '--apply']) == 1
    assert calls == [first, second]
    output = capsys.readouterr()
    assert 'PRIVATE' not in output.out + output.err


def test_missing_installed_writer_cannot_fall_back_to_a_module_in_data(tmp_path, monkeypatch):
    data = tmp_path / 'Data'
    library = data / '.datacore/modules/ventures/lib'
    library.mkdir(parents=True)
    (library / 'heartbeat_state.py').write_text('raise AssertionError("data code executed")\n')
    installed = tmp_path / 'installed/.datacore/lib'
    installed.mkdir(parents=True)
    monkeypatch.setenv('DATACORE_ROOT', str(data))
    monkeypatch.setattr(migration, 'LIB', installed)
    with pytest.raises(RuntimeError):
        migration._state_writer()


@pytest.mark.parametrize('aliased', [False, True])
def test_matching_installed_writer_is_loaded_without_searching_data(tmp_path, monkeypatch, aliased):
    installed = tmp_path / 'installed/.datacore/lib'
    installed.mkdir(parents=True)
    library = installed.parent / 'modules/ventures/lib'
    library.mkdir(parents=True)
    (library / '__init__.py').write_text('')
    source = library / 'heartbeat_state.py'
    if aliased:
        other = tmp_path / 'mutable.py'
        other.write_text('raise AssertionError("aliased code executed")\n')
        source.symlink_to(other)
    else:
        source.write_text('CONTRACT = "installed fixture"\n')
    monkeypatch.setattr(migration, 'LIB', installed)
    if aliased:
        with pytest.raises((OSError, ValueError, RuntimeError)):
            migration._state_writer()
    else:
        assert migration._state_writer().CONTRACT == 'installed fixture'
