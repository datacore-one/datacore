from concurrent.futures import ThreadPoolExecutor
import errno
import hashlib
import os
import subprocess
import sys

import pytest
from archive_files import archive_file
from org_transaction import serialized, watch_file, write_org_text
from safe_move import rename_noreplace


def test_atomic_move_cannot_replace_an_existing_file(tmp_path):
    source, destination = tmp_path / 'source', tmp_path / 'destination'
    source.write_bytes(b'new'); destination.write_bytes(b'old')
    with pytest.raises(FileExistsError):
        rename_noreplace(source, destination)
    assert source.read_bytes() == b'new' and destination.read_bytes() == b'old'


def test_competing_archives_preserve_every_version(tmp_path):
    files = [tmp_path / f'input-{number}.md' for number in range(8)]
    for number, file in enumerate(files):
        file.write_text(f'version {number}')
    def move(path):
        return archive_file(path, tmp_path / 'archive' / 'same.md', root=tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        archived = list(pool.map(move, files))
    assert len(set(archived)) == 8
    assert {p.read_text() for p in archived} == {f'version {n}' for n in range(8)}
    assert not any(path.exists() for path in files)


def test_failed_metadata_publication_restores_move_and_existing_metadata(tmp_path):
    source, destination, metadata = tmp_path/'source.md', tmp_path/'archive'/'source.md', tmp_path/'metadata'
    source.write_text('original'); metadata.write_text('original metadata')
    @serialized
    def mutation():
        archive_file(source, destination, root=tmp_path)
        watch_file(metadata)
        write_org_text(metadata, 'changed')
        raise OSError('publication failed')
    with pytest.raises(OSError, match='publication'):
        mutation()
    assert source.read_text() == 'original' and not destination.exists()
    assert metadata.read_text() == 'original metadata'


def test_process_exit_after_move_recovers_original_on_next_command(tmp_path):
    source, destination = tmp_path/'source.md', tmp_path/'archive'/'source.md'
    source.write_text('must survive')
    code = '''from pathlib import Path
import os
from org_transaction import serialized
from archive_files import archive_file
@serialized
def mutate():
    archive_file(Path(os.environ['SOURCE']), Path(os.environ['DEST']), root=Path(os.environ['ROOT']))
    os._exit(23)
mutate()
'''
    env = dict(os.environ, SOURCE=str(source), DEST=str(destination), ROOT=str(tmp_path),
               PYTHONPATH=str(__import__('pathlib').Path(__file__).parents[1]))
    result = subprocess.run([sys.executable, '-c', code], env=env, timeout=10)
    assert result.returncode == 23 and destination.read_text() == 'must survive'
    serialized(lambda: None)()
    assert source.read_text() == 'must survive' and not destination.exists()


def test_stale_classification_and_cross_space_paths_preserve_source(tmp_path):
    source = tmp_path/'source.md'; source.write_text('new content')
    with pytest.raises(ValueError, match='changed'):
        archive_file(source, tmp_path/'archive.md', root=tmp_path, expected_hash=hashlib.sha256(b'old').hexdigest())
    with pytest.raises(ValueError, match='outside'):
        archive_file(source, tmp_path.parent/'escape.md', root=tmp_path)
    assert source.read_text() == 'new content'


def test_competing_destination_is_retained_without_stranding_recovery(tmp_path,monkeypatch):
    import safe_move
    import org_transaction
    from archive_files import archive_file
    source=tmp_path/'source.md';source.write_text('source bytes')
    target=tmp_path/'archive.md'
    def race(src,dst):
        dst.write_text('independent file')
        raise FileExistsError('competing publication')
    monkeypatch.setattr(safe_move,'rename_noreplace',race)
    with pytest.raises(FileExistsError):
        archive_file(source,target,root=tmp_path)
    assert source.read_text() == 'source bytes'
    assert target.read_text() == 'independent file'
    assert not org_transaction.journal_path().exists()


def test_retired_triage_entry_point_cannot_overwrite_a_second_collision(tmp_path,monkeypatch):
    import importlib.util
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'archive/stub_triage.py'
    spec=importlib.util.spec_from_file_location('audit_old_triage',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    archive=tmp_path/'archive';archive.mkdir()
    (archive/'same.md').write_text('old first')
    (archive/'same_1.md').write_text('old second')
    source=tmp_path/'same.md';source.write_text('new complete content')
    monkeypatch.setattr(module,'DATA_ROOT',tmp_path);monkeypatch.setattr(module,'ARCHIVE_DIR',archive)
    assert module.execute_delete([(1,'same',str(source),0,'synthetic')],dry_run=False) == 1
    assert (archive/'same.md').read_text() == 'old first'
    assert (archive/'same_1.md').read_text() == 'old second'
    assert any(p.read_text() == 'new complete content' for p in archive.glob('*.md'))
