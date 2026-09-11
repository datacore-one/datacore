"""A shared prefix is not evidence that complete files are duplicates."""
import pytest
import nightshift_classifier as classifier


def test_different_tails_and_missing_digests_never_deduplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(classifier, 'DATA_DIR', tmp_path)
    prefix = '---\ntitle: Same report\n---\n' + 'shared text ' * 300
    paths = [tmp_path / name for name in ('first.md', 'second.md')]
    for index, path in enumerate(paths):
        path.write_text(prefix + f'\nDistinct conclusion {index}')
    records = classifier.build_records([{'path': path, 'detection': 'prefix'} for path in paths])
    assert len({record.content_hash for record in records}) == 2
    assert not any(record.category == 'duplicate' for record in classifier.dedup_records(records))
    for record in records:
        record.content_hash = ''
    assert not any(record.category == 'duplicate' for record in classifier.dedup_records(records))


def test_unreadable_source_does_not_become_an_empty_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(classifier, 'DATA_DIR', tmp_path)
    with pytest.raises(OSError, match='unreadable'):
        classifier.build_records([{'path': tmp_path / 'missing.md', 'detection': 'prefix'}])
